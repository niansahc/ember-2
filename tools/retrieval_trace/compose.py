"""
tools/retrieval_trace/compose.py

The parameterized scoring model: activations plus a parameter vector in,
per-stage scores out.

This is a MODEL of src/retrieval/semantic_search.py and
src/context/ranker.py, not a second implementation of them. The difference
matters. A second implementation would be free to drift and nobody would
know; a model is checked against the thing it models every time a trace is
captured (capture.py validates each stage boundary and refuses to write a
trace that disagrees). If this file is wrong, capture fails. That is the
whole design.

Composition order, which is the part that is easy to get subtly wrong:

    s0 = raw cosine
    s1 = s0 + lexical + type + source_quality + query_intent    (retrieval)
    s2 = s1 * policy weight
    s3 = s2 + recency * recency_bias + preference terms
    s4 = s3 * tier                                              (policy)
    s5 = s4 * authorship                                        (authorship)
    s6 = s5 + project boost                                     (project)
    s7 = s6 + type + role + kind + prefix + length + tokens + recency   (rank)
    s8 = s7 * temporal decay                                    (decay, final)

Two things about that order are load-bearing and were the subject of the
#204 grill. The tier multiply lands at s4, BEFORE the additive pile at s7,
so a cold discount only ever shrinks the cosine-derived half of a score and
leaves the constant half untouched. And the decay multiply lands last, on
everything, so `0.3 * 0.10 = 0.03` is reachable for an aged cold record.
Neither is a bug this file may quietly fix: the model has to be wrong in
exactly the ways the system is wrong, or the sensitivity analysis describes
a system nobody is running.
"""

from __future__ import annotations

from dataclasses import dataclass

from .params import ReplayParams
from .schema import CandidateTrace

STAGE_RETRIEVAL = "retrieval"
STAGE_POLICY = "policy"
STAGE_AUTHORSHIP = "authorship"
STAGE_PROJECT = "project"
STAGE_RANK = "rank"
STAGE_DECAY = "decay"

STAGES: tuple[str, ...] = (
    STAGE_RETRIEVAL,
    STAGE_POLICY,
    STAGE_AUTHORSHIP,
    STAGE_PROJECT,
    STAGE_RANK,
    STAGE_DECAY,
)


@dataclass
class Composition:
    """Per-stage scores plus every individual term that produced them."""

    stage_scores: dict[str, float]
    terms: dict[str, float]

    @property
    def final(self) -> float:
        return self.stage_scores[STAGE_DECAY]


def _recency_value(bucket: str, p: ReplayParams) -> float:
    return p[f"recency.{bucket}"] if bucket != "unparsed" else p["recency.unparsed"]


def compose(candidate: CandidateTrace, p: ReplayParams, policy_name: str) -> Composition:
    """Recompute one candidate's score from its activations."""
    terms: dict[str, float] = {}
    stages: dict[str, float] = {}

    # ---------------------------------------------------------------- retrieval
    r = candidate.retrieval
    if r.applies:
        terms["raw_cosine"] = r.raw_cosine

        # Grouped, not flattened. semantic_search adds three aggregates --
        # lexical_relevance_bonus, source_quality_adjustment,
        # query_intent_adjustment -- each of which accumulates internally
        # first. Float addition is not associative, so summing the leaves in
        # a different grouping lands a few ulps away from the shipped score
        # and "matches exactly" stops being true. The grouping here is the
        # shipped grouping, in the shipped order.
        lexical = 0.0
        if r.lexical_substring:
            terms["ret.lexical.substring"] = p["ret.lexical.substring"]
            lexical += p["ret.lexical.substring"]
        # min(hits * per_hit, cap): the cap is its own parameter and can be
        # crossed by perturbing either side, so both are applied rather than
        # folded into one effective value.
        term_hits = min(
            r.lexical_term_hits * p["ret.lexical.term_hit"], p["ret.lexical.term_cap"]
        )
        terms["ret.lexical.term_hit"] = term_hits
        lexical += term_hits
        if r.lexical_entity_hits:
            entity = min(
                r.lexical_entity_hits * p["ret.lexical.entity_hit"],
                p["ret.lexical.entity_cap"],
            )
            terms["ret.lexical.entity_hit"] = entity
            lexical += entity

        type_term = p[f"ret.type.{r.type_branch}"]
        terms["ret.type"] = type_term

        quality = 0.0
        if r.quality_role == "user":
            terms["ret.quality.role"] = p["ret.quality.role_user"]
            quality += p["ret.quality.role_user"]
        elif r.quality_role == "assistant":
            terms["ret.quality.role"] = p["ret.quality.role_assistant"]
            quality += p["ret.quality.role_assistant"]
        question_term = (
            p["ret.quality.question"] if r.quality_is_question else p["ret.quality.not_question"]
        )
        terms["ret.quality.question"] = question_term
        quality += question_term
        if r.quality_clarification:
            terms["ret.quality.clarification"] = p["ret.quality.clarification"]
            quality += p["ret.quality.clarification"]
        if r.quality_experience:
            terms["ret.quality.experience"] = p["ret.quality.experience"]
            quality += p["ret.quality.experience"]
        if r.quality_summary:
            terms["ret.quality.summary"] = p["ret.quality.summary"]
            quality += p["ret.quality.summary"]

        intent = 0.0
        if r.intent_reflective:
            key = {
                "conversation": "ret.intent.reflective_conversation",
                "reflection": "ret.intent.reflective_reflection",
                "ingested": "ret.intent.reflective_ingested",
            }.get(r.type_branch)
            if key:
                terms["ret.intent.type"] = p[key]
                intent += p[key]
            if r.content_prefix == "user":
                terms["ret.intent.prefix"] = p["ret.intent.reflective_user_prefix"]
                intent += p["ret.intent.reflective_user_prefix"]
            elif r.content_prefix == "assistant":
                terms["ret.intent.prefix"] = p["ret.intent.reflective_assistant_prefix"]
                intent += p["ret.intent.reflective_assistant_prefix"]
        elif r.intent_task:
            key = {
                "ingested": "ret.intent.task_ingested",
                "conversation": "ret.intent.task_conversation",
            }.get(r.type_branch)
            if key:
                terms["ret.intent.type"] = p[key]
                intent += p[key]

        score = r.raw_cosine
        score += lexical
        score += type_term
        score += quality
        score += intent
    else:
        # Reflection channel: the base score is a Jaccard overlap computed in
        # ContextRetriever.get_reflection_items, with no retrieval terms.
        score = candidate.stage_scores.get(STAGE_RETRIEVAL, 0.0)
        terms["reflection_base"] = score
    stages[STAGE_RETRIEVAL] = score

    # ------------------------------------------------------------------ policy
    pol = candidate.policy
    weight = p.policy_field(policy_name, pol.weight_field, pol.weight_captured)
    terms[f"pol.{pol.weight_field}"] = weight
    score = score * weight

    bias = p.policy_field(policy_name, "recency_bias", pol.recency_bias_captured)
    if bias:
        contribution = _recency_value(pol.recency_bucket, p) * bias
        terms["pol.recency"] = contribution
        score += contribution
    if pol.prefer_experience_fired:
        terms["pol.prefer_experience"] = p["pol.prefer_experience"]
        score += p["pol.prefer_experience"]
    if pol.prefer_active_work_fired:
        terms["pol.prefer_active_work"] = p["pol.prefer_active_work"]
        score += p["pol.prefer_active_work"]
    if pol.exact_branch == "question":
        terms["pol.exact"] = p["pol.exact.question"]
        score += p["pol.exact.question"]
    elif pol.exact_branch == "other":
        terms["pol.exact"] = p["pol.exact.other"]
        score += p["pol.exact.other"]

    tier_factor = p[f"tier.{pol.tier_branch}"]
    terms["tier"] = tier_factor
    score = score * tier_factor
    stages[STAGE_POLICY] = score

    # -------------------------------------------------------------- authorship
    a = candidate.author
    if a.relational_query:
        factor = p[f"auth.{a.branch}"]
        terms["auth"] = factor
        score = score * factor
    stages[STAGE_AUTHORSHIP] = score

    # ----------------------------------------------------------------- project
    if a.project_match:
        terms["proj.boost"] = p["proj.boost"]
        score += p["proj.boost"]
    stages[STAGE_PROJECT] = score

    # -------------------------------------------------------------------- rank
    k = candidate.rank
    if k.reflection_path:
        terms["refl.base_discount"] = p["refl.base_discount"]
        score = score * p["refl.base_discount"]
        if k.reflection_short:
            terms["refl.short"] = p["refl.short"]
            score += p["refl.short"]
        contribution = _recency_value(k.recency_bucket, p) * p["refl.recency_scale"]
        terms["refl.recency"] = contribution
        score += contribution
    else:
        # Sequential, in _score_memory_item's own order, for the same
        # associativity reason as the retrieval group above.
        terms["rank.type"] = p[f"rank.type.{k.type_branch}"]
        score += terms["rank.type"]
        if k.role_branch != "none":
            terms["rank.role"] = p[f"rank.role.{k.role_branch}"]
            score += terms["rank.role"]
        if k.kind_branch != "none":
            terms["rank.kind"] = p[f"rank.kind.{k.kind_branch}"]
            score += terms["rank.kind"]
        if k.user_prefix:
            terms["rank.user_prefix"] = p["rank.user_prefix"]
            score += terms["rank.user_prefix"]
        if k.length_branch != "none":
            terms["rank.length"] = p[f"rank.len.{k.length_branch}"]
            score += terms["rank.length"]
        if k.tokens_lt5:
            terms["rank.tokens_lt5"] = p["rank.tokens_lt5"]
            score += terms["rank.tokens_lt5"]
        terms["rank.recency"] = _recency_value(k.recency_bucket, p)
        score += terms["rank.recency"]
    stages[STAGE_RANK] = score

    # ------------------------------------------------------------------- decay
    d = candidate.decay
    factor = p["decay.none"] if d.family == "none" else p[f"decay.{d.family}.{d.bucket}"]
    terms["decay"] = factor
    score = score * factor
    stages[STAGE_DECAY] = score

    return Composition(stage_scores=stages, terms=terms)
