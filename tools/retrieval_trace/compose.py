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
    s1 = s0 + lexical + query_intent                            (retrieval)
    s2 = s1 * policy weight
    s3 = s2 + preference terms
    s4 = s3 * tier                                              (policy)
         role predicate: SELECTION, not a score -- no stage
    s5 = s4 * authorship                                        (authorship)
    s6 = s5 + project boost                                     (project)
    s7 = s6 * prior                                             (rank, final)

WHAT ADR-044 CHANGED HERE, and what it did not

The previous version of this docstring recorded two properties as
load-bearing: that the tier multiply landed BEFORE the ranker's additive
pile, so a cold discount shrank only the cosine-derived half of a score;
and that a temporal-decay multiply landed last on everything, so
`0.3 * 0.10 = 0.03` was reachable for an aged cold record. Both were
defects rather than design, and both are gone -- the additive pile is one
bounded multiplier and there is no decay stage at all.

The rule those notes existed to protect has not changed and is the reason
this file is a MODEL and not a second implementation: it has to be wrong in
exactly the ways the system is wrong. capture.py checks every stage
boundary against the shipped functions at 1e-12 and refuses to write a
trace where they disagree, so a mistake here fails a capture rather than
biasing an analysis.

One residual, recorded rather than fixed. `proj.boost` still lands at s6,
AFTER the tier multiply at s4, so tier does not attenuate it -- the same
ordering defect ADR-044 decision 1 names, reduced from a whole additive
pile to one term. The prior at s7 does attenuate it. Not this file's to
change.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.context import prior as _prior

from .params import ReplayParams
from .schema import CandidateTrace

STAGE_RETRIEVAL = "retrieval"
STAGE_POLICY = "policy"
STAGE_AUTHORSHIP = "authorship"
STAGE_PROJECT = "project"
STAGE_RANK = "rank"

# STAGE_DECAY is gone. It was a real pipeline boundary -- the ranker
# multiplied by _temporal_decay_weight after scoring -- and ADR-044 decision 3
# absorbed that curve into TieringService, where it is a nightly input to tier
# rather than a query-time stage. There is nothing left to trace at that
# boundary, so the boundary is removed rather than kept at a constant 1.0.
STAGES: tuple[str, ...] = (
    STAGE_RETRIEVAL,
    STAGE_POLICY,
    STAGE_AUTHORSHIP,
    STAGE_PROJECT,
    STAGE_RANK,
)

# The final stage, named once so consumers do not have to know which it is.
STAGE_FINAL = STAGE_RANK


@dataclass
class Composition:
    """Per-stage scores plus every individual term that produced them."""

    stage_scores: dict[str, float]
    terms: dict[str, float]

    @property
    def final(self) -> float:
        return self.stage_scores[STAGE_FINAL]


def _recency_value(bucket: str, p: ReplayParams) -> float:
    return p.values.get(f"prior.recency.{bucket}", 1.0)


def compose(candidate: CandidateTrace, p: ReplayParams, policy_name: str) -> Composition:
    """Recompute one candidate's score from its activations."""
    terms: dict[str, float] = {}
    stages: dict[str, float] = {}

    # ---------------------------------------------------------------- retrieval
    r = candidate.retrieval
    if r.applies:
        terms["raw_cosine"] = r.raw_cosine

        # Grouped, not flattened. semantic_search adds two aggregates --
        # lexical_relevance_bonus and query_intent_adjustment -- each of which
        # accumulates internally first. Float addition is not associative, so
        # summing the leaves in a different grouping lands a few ulps away
        # from the shipped score and "matches exactly" stops being true. The
        # grouping here is the shipped grouping, in the shipped order.
        #
        # It used to be three aggregates. memory_type_adjustment and
        # source_quality_adjustment were the other two and are no longer
        # called, so both their groups are gone from the sum.
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

    # The recency * recency_bias contribution that stood here is gone with
    # ContextPolicy.recency_bias (ADR-044). It was the third additive copy of
    # the recency ladder.
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
    #
    # One multiply, built in prior.assemble's own order for the same
    # associativity reason as the retrieval group above. Both the individual
    # factors and the clamped product are recorded: the product is what the
    # score actually took, and the factors are what a sensitivity pass needs
    # to attribute it. Recording only the product would make every prior term
    # indistinguishable from every other.
    k = candidate.rank

    prior_factor = 1.0
    if k.kind_branch != "none":
        terms["prior.kind"] = p[f"prior.kind.{k.kind_branch}"]
        prior_factor *= terms["prior.kind"]
    if k.length_branch != "none":
        terms["prior.length"] = p[f"prior.len.{k.length_branch}"]
        prior_factor *= terms["prior.length"]
    terms["prior.recency"] = _recency_value(k.recency_bucket, p)
    prior_factor *= terms["prior.recency"]
    if k.reflection_path:
        terms["prior.reflection_discount"] = p["prior.reflection_discount"]
        prior_factor *= terms["prior.reflection_discount"]

    # The clamp is part of the contract, not a safety net, so the model has to
    # apply it -- a composition that skipped it would disagree with the
    # pipeline exactly when the bound is doing its job, which is the one case
    # that matters.
    clamped = _prior.clamp(prior_factor)
    terms["prior"] = clamped
    score = score * clamped
    stages[STAGE_RANK] = score

    return Composition(stage_scores=stages, terms=terms)
