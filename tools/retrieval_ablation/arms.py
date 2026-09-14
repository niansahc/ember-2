"""
tools/retrieval_ablation/arms.py

The ablation seam: run one stratum through the real pipeline under one arm's
treatment, and return what the pipeline delivered.

Design rule throughout: neutralise a mechanism by removing its INPUT, not by
restating its constants. Every ablation below is constant-free, so it cannot
drift when a weight is retuned.

  - tier off        -> set item.tier = "hot". ranker reads tier at exactly one
                       place (apply_policy) and nowhere else, so this is exact.
  - type boost off  -> swap item.item_type to a sentinel during the scoring
                       call. Inside _score_memory_item, item_type is read once
                       and used only for the type boost.
  - weight split off-> set policy.reflection_weight = policy.memory_weight.
  - decay off       -> _temporal_decay_weight returns 1.0 uniformly. NOT
                       collapsed to _DEFAULT_DECAY, which would strip the
                       no-decay exemption that `ingested` and `profile` rely on
                       and can flip delta_T's sign.
  - profile privilege off
                    -> relabel profile as "reference". Both are in
                       _NO_DECAY_TYPES, so decay is unchanged, but "reference"
                       is not "profile" so it loses the guaranteed slot, the
                       type-gate bypass and the tier bypass. Relabelling to a
                       decaying type instead would have applied a 10x decay
                       change as a side effect and contaminated the arm.

The retrieval-stage score is computed here rather than patched, because
patching `retriever.retrieve` bypasses `semantic_search` entirely -- which
would silently drop the retrieval-stage half of typing AND make the lexical
baseline unimplementable. The real scoring functions are called directly.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from datetime import datetime
from unittest.mock import patch

from src.context.models import ContextItem
from src.retrieval.semantic_search import (
    extract_query_terms,
    lexical_relevance_bonus,
    memory_type_adjustment,
    query_intent_adjustment,
    source_quality_adjustment,
)

from .corpus import FIXTURES, FIXTURES_BY_ID, Fixture, Stratum
from .metrics import Delivered

# An item_type that scores +0.00 in _score_memory_item's type ladder. Any
# unlisted value falls through; this one is named so it cannot be mistaken for
# a real type in a debugger.
NEUTRAL_ITEM_TYPE = "__type_ablated__"

# query_intent_adjustment mixes a type-keyed term with a content-prefix term.
# Passing a type that both of its branches ignore isolates the prefix term, so
# the type contribution can be removed by subtraction rather than by
# reimplementing the function's constants. `journal` is ignored by both
# branches (reflective handles conversation/reflection/ingested; task handles
# ingested/conversation).
TYPE_NEUTRAL_FOR_INTENT = "journal"


@dataclass(frozen=True)
class Arm:
    """One treatment.

    Retrieval-stage switches (what the harness adds to the base cosine):
      type_terms     -- memory_type_adjustment + the type half of
                        query_intent_adjustment
      lexical_terms  -- lexical_relevance_bonus (substring, term hits, entity)
      source_quality -- source_quality_adjustment (role/content heuristics)

    Post-retrieval switches:
      ranker_stages  -- run the ranker at all. False is the naive baseline:
                        deliver by retrieval score alone.
      type_scoring   -- policy weight split + _score_memory_item type boost
      type_policy    -- type-gate eligible/suppress filtering + profile
                        guaranteed slots
      tier_scoring   -- cold/warm multipliers
      decay          -- temporal decay curves
      recency        -- _recency_boost, the ADDITIVE freshness bonus

    `recency` and `decay` are two different mechanisms and need separate
    switches. _recency_boost adds a bonus for being new (ranker.py:46, :317,
    :340, scaled by policy.recency_bias); _temporal_decay_weight multiplies a
    penalty for being old, and exempts profile/reference/ingested. Ablating
    decay leaves the recency bonus fully intact, which is why the recency-bait
    distractors showed a measured swing of exactly 0.000 under A_decay-off:
    they were baiting a lever no arm removed, so their leakage counts could
    not attribute to anything. The corpus test that pins bait reachability is
    what surfaced that.
    """

    name: str
    label: str
    type_terms: bool = True
    lexical_terms: bool = True
    source_quality: bool = True
    ranker_stages: bool = True
    type_scoring: bool = True
    type_policy: bool = True
    tier_scoring: bool = True
    decay: bool = True
    recency: bool = True


ARMS: tuple[Arm, ...] = (
    Arm(name="A0_FULL", label="full pipeline (reference)"),
    Arm(
        name="A_T-off_scoring",
        label="typed scoring off, typed gating held",
        type_terms=False,
        type_scoring=False,
    ),
    Arm(
        name="A_T-off_policy",
        label="typed scoring, typed gating and profile privilege all off",
        type_terms=False,
        type_scoring=False,
        type_policy=False,
    ),
    Arm(
        name="A_H-off",
        label="tiering off (one-channel: only get_memory_items carries tier)",
        tier_scoring=False,
    ),
    Arm(
        name="A_decay-off",
        label="temporal decay off (second type-keyed age mechanism)",
        decay=False,
    ),
    Arm(
        name="A_recency-off",
        label="additive freshness bonus off (distinct from temporal decay)",
        recency=False,
    ),
    # The lexical and quality terms needed isolating arms of their own. Without
    # them the lexical, entity and experience distractor classes had no arm that
    # removed the lever they bait, so their leakage counts could not attribute
    # to anything: the only arms that dropped the lexical term (A_NAIVE_*) also
    # switch the ranker off, so any movement was confounded. Note the lexical
    # term is the LARGEST single lever in the system -- 0.10 substring + 0.18
    # term cap + 0.40 entity cap -- so leaving it unisolated meant the biggest
    # mechanism in the pipeline was the one the eval could say least about.
    Arm(
        name="A_lexical-off",
        label="lexical and entity terms off, ranker otherwise intact",
        lexical_terms=False,
    ),
    Arm(
        name="A_quality-off",
        label="source-quality (role and content-kind) scoring off",
        source_quality=False,
    ),
    Arm(
        name="A_T-off_H-off",
        label="typed scoring and tiering both off",
        type_terms=False,
        type_scoring=False,
        tier_scoring=False,
    ),
    Arm(
        name="A_NAIVE_cosine",
        label="bare cosine, no ranker",
        type_terms=False,
        lexical_terms=False,
        source_quality=False,
        ranker_stages=False,
    ),
    Arm(
        name="A_NAIVE_plus_lexical",
        label="cosine plus lexical and entity terms, no typing",
        type_terms=False,
        lexical_terms=True,
        source_quality=False,
        ranker_stages=False,
    ),
)

ARMS_BY_NAME: dict[str, Arm] = {a.name: a for a in ARMS}


class PristineScoreError(AssertionError):
    """A candidate reached an arm carrying a score from an earlier run."""


# ---------------------------------------------------------------------------
# Retrieval-stage score
# ---------------------------------------------------------------------------

def _type_component(query: str, memory_type: str, content: str) -> float:
    """The type-keyed part of query_intent_adjustment, by subtraction.

    Derived from the real function rather than reimplemented, so it stays
    correct if its constants are retuned.
    """
    full = query_intent_adjustment(query, memory_type, content)
    prefix_only = query_intent_adjustment(query, TYPE_NEUTRAL_FOR_INTENT, content)
    return full - prefix_only


def retrieval_score(fixture: Fixture, stratum: Stratum, arm: Arm) -> float:
    """What semantic_search would have produced for this fixture under this arm.

    Base cosine is stipulated by the corpus; every adjustment is the real
    production function.
    """
    normalized_query = " ".join(stratum.query.lower().split())
    normalized_content = " ".join(fixture.text.lower().split())
    score = stratum.cosine(fixture.id)

    if arm.type_terms:
        score += memory_type_adjustment(fixture.memory_type)
        score += _type_component(stratum.query, fixture.memory_type, normalized_content)

    # The content-prefix half of query_intent_adjustment is role/lexical, not
    # type, so it travels with the lexical switch.
    if arm.lexical_terms:
        score += query_intent_adjustment(
            stratum.query, TYPE_NEUTRAL_FOR_INTENT, normalized_content
        )
        score += lexical_relevance_bonus(
            normalized_query,
            extract_query_terms(normalized_query),
            normalized_content,
            raw_query=stratum.query,
        )

    if arm.source_quality:
        score += source_quality_adjustment(
            normalized_content, {"role": fixture.role} if fixture.role else {}
        )

    return score


# get_profile_items runs its own search with limit=3 on a normal query and
# limit=8 on an identity query, so at most that many profile records can ever
# reach memory_items. Injecting every profile fixture would hand the pipeline
# more profile candidates than production can produce -- and because profile
# takes guaranteed slots ahead of the limit, that alone would crowd real memory
# out of every arm and make three of them identical for the wrong reason.
_PROFILE_LIMIT_DEFAULT = 3
_PROFILE_LIMIT_IDENTITY = 8
_IDENTITY_STRATA = {"default_identity_question"}


def _candidate_fixtures(stratum: Stratum) -> list[Fixture]:
    """The pool as the retriever could actually have produced it."""
    limit = (
        _PROFILE_LIMIT_IDENTITY
        if stratum.name in _IDENTITY_STRATA
        else _PROFILE_LIMIT_DEFAULT
    )
    profile = [f for f in FIXTURES if f.memory_type == "profile"]
    profile.sort(key=lambda f: stratum.cosine(f.id), reverse=True)
    kept_profile = {f.id for f in profile[:limit]}
    return [
        f for f in FIXTURES
        if f.memory_type != "profile" or f.id in kept_profile
    ]


def build_candidates(stratum: Stratum, arm: Arm) -> list[ContextItem]:
    """Deep-copied candidate set for one (stratum, arm).

    Deep copy is mandatory: apply_policy, apply_authorship_scoring,
    apply_project_boost and rank all mutate ContextItem.score in place, and two
    of them return the same list object they were handed. Reusing items across
    arms would compound every arm's multipliers into the next.
    """
    items: list[ContextItem] = []
    for fixture in _candidate_fixtures(stratum):
        fixture = copy.deepcopy(fixture)
        cosine = stratum.cosine(fixture.id)
        score = retrieval_score(fixture, stratum, arm)

        memory_type = fixture.memory_type
        if not arm.type_policy and memory_type == "profile":
            # Strip profile privilege without touching decay -- see module
            # docstring.
            memory_type = "reference"

        metadata: dict = {"raw_score": cosine, "fixture_id": fixture.id}
        if fixture.role:
            metadata["role"] = fixture.role
        if fixture.content_kind:
            metadata["content_kind"] = fixture.content_kind

        items.append(
            ContextItem(
                id=fixture.id,
                content=fixture.text,
                source=memory_type,
                item_type=memory_type,
                memory_type=memory_type,
                score=score,
                timestamp=fixture.timestamp(),
                tags=[],
                metadata=metadata,
                tier="hot" if not arm.tier_scoring else fixture.tier,
                authorship="first_person",
            )
        )
    return items


def assert_pristine(items: list[ContextItem], stratum: Stratum, arm: Arm) -> None:
    """Every candidate must carry exactly its arm-entry score.

    This is the guard against the in-place mutation described in
    build_candidates. It fires on a score that has already been through a
    ranking stage.
    """
    for item in items:
        expected = retrieval_score(FIXTURES_BY_ID[item.id], stratum, arm)
        if abs(item.score - expected) > 1e-9:
            raise PristineScoreError(
                f"{arm.name}/{stratum.name}: {item.id} entered at {item.score!r}, "
                f"expected {expected!r}. A previous arm's multipliers have "
                f"compounded into this one."
            )
        if abs(item.metadata.get("raw_score", -1) - stratum.cosine(item.id)) > 1e-9:
            raise PristineScoreError(
                f"{arm.name}/{stratum.name}: {item.id} raw_score was mutated."
            )


# ---------------------------------------------------------------------------
# Pipeline patches
# ---------------------------------------------------------------------------

# Probe queries that route deterministically to each policy through
# classify_query's keyword branches. Using the real classifier (with only its
# LLM stage patched out) means the policies carry their REAL constants --
# reconstructing ContextPolicy by hand would duplicate ten sets of weights that
# could silently drift from the source.
_POLICY_PROBES: dict[str, str] = {
    "status_state": "what is my current focus",
    "reflective": "what patterns do you notice",
    "factual_recall": "when did i say that",
    # Needs a recent marker AND an activity marker, while avoiding the
    # reflective set which is checked first -- "what have i been" is itself a
    # reflective marker, so the obvious phrasing routes to reflective.
    "recent_activity": "am i making progress on it currently",
    "recent": "what happened lately",
    "activity": "what am i building",
    "default": "explain how tides work",
    # task_status is reached through its own marker set, checked before the
    # state markers.
    "task_status": "what is the status of that task",
}


def base_policy(policy_name: str):
    """The real ContextPolicy for a name, obtained from classify_query itself.

    classify_intent is patched to a non-internet label so no LLM call happens;
    every other branch of the classifier is the production one. The assertion
    is the point: if a marker list is retuned so a probe stops routing where it
    used to, this fails loudly instead of silently handing back the default
    policy and quietly flattening a stratum.
    """
    from src.context.policies import classify_query

    probe = _POLICY_PROBES[policy_name]
    with patch("src.context.policies.classify_intent", return_value="vault_answerable"):
        policy = classify_query(probe)
    if policy.name != policy_name:
        raise AssertionError(
            f"probe {probe!r} routed to {policy.name!r}, expected {policy_name!r}. "
            "A marker list in src/context/policies.py has changed."
        )
    return policy


def _policy_for(stratum: Stratum, arm: Arm):
    """The stratum's pinned policy, adjusted for the arm.

    Pinned rather than classified: classify_query calls the LLM intent
    classifier (policies.py:282), which would make the eval non-deterministic
    and Ollama-dependent, and intent classification is not under test.

    diversity is forced False in EVERY arm as a controlled variable.
    _select_diverse_memory is a hard round-robin quota that forces a 2/2/2
    type split at limit 6 regardless of score, which would clamp the
    composition metric identically in every arm and hide the treatment. What
    the quota would have selected is reported separately.
    """
    policy = base_policy(stratum.policy_name)
    changes: dict = {"diversity": False}
    if not arm.type_scoring:
        # Equalise the reflection/memory weight split without naming either
        # constant.
        changes["reflection_weight"] = policy.memory_weight
    if not arm.type_policy:
        changes["eligible_memory_types"] = None
        changes["suppress_memory_types"] = []
    return replace(policy, **changes)


def _patched_type_gate(arm: Arm):
    """_apply_type_gate replacement.

    Two changes. First, the min_score floor reads metadata.raw_score instead of
    item.score: the harness varies the pre-pipeline score per arm, so gating on
    the adjusted score would make the filter a treatment-detector -- a
    lower-scoring arm would lose candidates before ranking even began. Second,
    under type_policy=False the eligible/suppress filtering is skipped.
    """

    def _gate(self, items, policy):
        floor = policy.min_score
        keep = []
        for item in items:
            raw = (getattr(item, "metadata", {}) or {}).get("raw_score", 0.0)
            if raw < floor:
                continue
            if arm.type_policy:
                mem_type = getattr(item, "memory_type", None)
                if mem_type != "profile":
                    if policy.suppress_memory_types and mem_type in policy.suppress_memory_types:
                        continue
                    if policy.eligible_memory_types is not None and (
                        mem_type not in policy.eligible_memory_types
                    ):
                        continue
            keep.append(item)
        return keep

    return _gate


def _patched_score_memory_item(original, arm: Arm):
    """Neutralise the type boost by swapping item_type for the duration of the
    call. item_type is read once inside _score_memory_item and used only for
    the type ladder, so this removes exactly that term and nothing else."""

    def _scored(self, item):
        if arm.type_scoring:
            return original(self, item)
        real_type = item.item_type
        item.item_type = NEUTRAL_ITEM_TYPE
        try:
            return original(self, item)
        finally:
            item.item_type = real_type

    return _scored


# ---------------------------------------------------------------------------
# Running one cell
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CellResult:
    """What one (stratum, arm) produced.

    `delivered` is the packet the model would see. `ranked` is the ranker's
    full verdict before the per-policy limit and the profile partition.

    Both are needed to separate two very different claims. Profile takes
    guaranteed slots ahead of the limit -- three of them on every query,
    because get_profile_items' min_score floor is dead -- which can leave as
    few as one non-profile slot. A mechanism can then reorder the tail
    substantially and change the delivered set not at all. Measuring only
    `delivered` would report that as "the mechanism does nothing", when the
    truthful statement is "the mechanism is masked by profile crowding at this
    limit". The first is a claim about the design; the second is a claim about
    a different defect.
    """

    delivered: list[Delivered]
    ranked: list[Delivered]


def run_cell(stratum: Stratum, arm: Arm) -> CellResult:
    """Run one (stratum, arm) and return what the pipeline produced.

    Reported memory_type and tier come from the FIXTURE, not from the item the
    pipeline saw, so composition reflects what the record actually is even in
    arms that relabel or flatten those fields to ablate a mechanism.
    """
    from src.context.ranker import ContextRanker
    from src.context.service import ContextService

    candidates = build_candidates(stratum, arm)
    assert_pristine(candidates, stratum, arm)

    if not arm.ranker_stages:
        # Naive baseline: no ranker, no gating, no policy. Rank by retrieval
        # score and cut at the service limit the pinned policy would have used,
        # so the comparison is not decided by how many items each arm returns.
        limit = ContextService.__new__(ContextService)._memory_limit_for_policy(
            stratum.policy_name
        )
        ordered = sorted(candidates, key=lambda i: i.score, reverse=True)
        return CellResult(
            delivered=_to_delivered(ordered[:limit]),
            ranked=_to_delivered(ordered),
        )

    service = ContextService()
    policy = _policy_for(stratum, arm)
    original_score = ContextRanker._score_memory_item
    original_decay = ContextRanker._temporal_decay_weight
    original_recency = ContextRanker._recency_boost
    original_rank = ContextRanker.rank
    captured: list[Delivered] = []

    def _capturing_rank(self, memory_items, reflection_items):
        ranked_memory, ranked_reflections = original_rank(
            self, memory_items, reflection_items
        )
        captured.clear()
        captured.extend(_to_delivered(ranked_memory))
        return ranked_memory, ranked_reflections

    def _retrieve(query, *args, **kwargs):
        # state, task, memory, reflection, query_embedding. Everything goes in
        # the memory channel: metrics are over memory_items, and reflections
        # arriving through the separate reflection channel are scored by a
        # different, much shorter function on an incompatible scale.
        return [], [], candidates, [], None

    stack = [
        patch.object(service.retriever, "retrieve", side_effect=_retrieve),
        patch("src.context.service.classify_query", return_value=policy),
        patch.object(ContextService, "_apply_type_gate", _patched_type_gate(arm)),
        patch.object(
            ContextRanker,
            "_score_memory_item",
            _patched_score_memory_item(original_score, arm),
        ),
        patch.object(ContextRanker, "rank", _capturing_rank),
    ]
    if not arm.decay:
        stack.append(
            patch.object(ContextRanker, "_temporal_decay_weight", lambda self, item: 1.0)
        )
    if not arm.recency:
        # 0.0, not 1.0: _recency_boost is ADDITIVE, so the neutral element is
        # zero. Returning 1.0 would hand every record a flat bonus and change
        # the scale rather than remove the mechanism.
        stack.append(
            patch.object(ContextRanker, "_recency_boost", lambda self, timestamp: 0.0)
        )

    for ctx in stack:
        ctx.start()
    try:
        packet = service.build_context(stratum.query, skip_web_search=True)
    finally:
        for ctx in reversed(stack):
            ctx.stop()
        ContextRanker._score_memory_item = original_score
        ContextRanker._temporal_decay_weight = original_decay
        ContextRanker._recency_boost = original_recency
        ContextRanker.rank = original_rank

    return CellResult(
        delivered=_to_delivered(packet.memory_items),
        ranked=list(captured),
    )


def _to_delivered(items) -> list[Delivered]:
    delivered: list[Delivered] = []
    for item in items:
        fixture = FIXTURES_BY_ID.get(item.id)
        delivered.append(
            Delivered(
                id=item.id,
                score=float(item.score),
                memory_type=fixture.memory_type if fixture else (item.memory_type or ""),
                tier=fixture.tier if fixture else (item.tier or "hot"),
            )
        )
    return delivered


def residual_quota(stratum: Stratum, arm: Arm, delivered: list[Delivered]) -> dict:
    """What the diversity quota would have done, for the strata where it is
    normally on.

    diversity is forced off in every arm so it cannot clamp composition
    identically everywhere, but the brief requires the residual be reported
    rather than simply dropped.
    """
    from src.context.policies import ContextPolicy  # noqa: F401

    normally_on = stratum.policy_name in {"reflective", "recent_activity", "recent", "activity"}
    if not normally_on:
        return {"quota_normally_active": False}

    groups: dict[str, int] = {"conversation": 0, "ingested": 0, "other": 0}
    for item in delivered:
        key = item.memory_type if item.memory_type in ("conversation", "ingested") else "other"
        groups[key] += 1
    return {
        "quota_normally_active": True,
        "delivered_group_split": groups,
        "quota_would_force": "round-robin conversation/ingested/other, one per pass",
    }


# ---------------------------------------------------------------------------
# Measured lever attribution
# ---------------------------------------------------------------------------
# Which lever a distractor class is LABELLED with. The label is a design
# intention, not a measurement: a bait can be labelled a type bait and in fact
# be carried by recency. `measure_lever_attribution` reports which lever
# actually moves it, so the leakage metric never has to be trusted on the label
# alone -- the report prints both side by side.

LEVER_FOR_CLASS: dict[str, str] = {
    "type_boost_bait": "A_T-off_scoring",
    "reflection_weight_bait": "A_T-off_scoring",
    "decay_bait": "A_decay-off",
    "recency_bait": "A_recency-off",
    "tier_bait": "A_H-off",
    "lexical_bait": "A_lexical-off",
    "entity_bait": "A_lexical-off",
    "experience_bait": "A_quality-off",
}

# Recency and decay are NOT separable by construction, and no corpus can make
# them so. Both key on the same input -- the record's age -- but pull in
# opposite directions on different sides of the pair: the additive freshness
# bonus lifts a new distractor, and the multiplicative decay penalty pushes down
# the older relevant record it displaces. A fresh bait is therefore helped twice
# over by two mechanisms that a minimal pair cannot tell apart, because holding
# age constant disables both at once. This is a property of the pipeline, not a
# limitation of the fixtures, and the report says so rather than tuning it away.
ENTANGLED_LEVERS: tuple[frozenset[str], ...] = (
    frozenset({"A_recency-off", "A_decay-off"}),
)


def _entangled_with(arm_name: str) -> set[str]:
    out = {arm_name}
    for group in ENTANGLED_LEVERS:
        if arm_name in group:
            out |= set(group)
    return out


def measure_lever_attribution(stratum: Stratum) -> list[dict]:
    """For every designed bait in this stratum, which lever actually carries it.

    Returns one row per (bait, victim) pair with the relative swing of every
    lever. "Relative" matters: a lever that lowers bait and victim equally has
    separated nothing, so the swing is always measured as the bait's movement
    minus the victim's.
    """
    from .corpus import BAIT_TARGETS, FIXTURES, FIXTURES_BY_ID

    designed = BAIT_TARGETS.get(stratum.name, ())
    if not designed:
        return []

    full = {d.id: d.score for d in run_cell(stratum, ARMS_BY_NAME["A0_FULL"]).ranked}
    relevant = [f.id for f in FIXTURES if stratum.grade(f.id) >= 2 and f.id in full]
    if not relevant:
        return []

    lever_arms = sorted(set(LEVER_FOR_CLASS.values()))
    off_scores = {
        name: {d.id: d.score for d in run_cell(stratum, ARMS_BY_NAME[name]).ranked}
        for name in lever_arms
    }

    rows: list[dict] = []
    for bait_id in designed:
        if bait_id not in full:
            continue
        outranked = [i for i in relevant if full[bait_id] > full[i]]
        if not outranked:
            # Below every relevant record: it cannot displace anything here, so
            # there is no attribution question to answer.
            continue

        # Evaluated against EVERY relevant record the bait currently outranks,
        # taking each lever's best separation. Scoring against only the nearest
        # victim systematically picks a same-type neighbour -- a type boost
        # moves two conversations by the same amount and separates neither --
        # which makes a lever that works perfectly well look inert. The question
        # reachability actually asks is whether the lever can push the bait back
        # below ANY of the records it displaced.
        def _swing_against(arm_name: str, victim_id: str) -> float:
            off = off_scores[arm_name]
            if bait_id not in off or victim_id not in off:
                return 0.0
            return (full[bait_id] - off[bait_id]) - (
                full[victim_id] - off[victim_id]
            )

        swings: dict[str, float] = {}
        best_victim: dict[str, str] = {}
        for name in lever_arms:
            scored = [(_swing_against(name, v), v) for v in outranked]
            value, victim_id = max(scored)
            swings[name] = value
            best_victim[name] = victim_id

        cls = FIXTURES_BY_ID[bait_id].distractor_class
        target = LEVER_FOR_CLASS[cls]
        dominant = max(swings, key=lambda a: swings[a])
        victim = best_victim[target]
        rows.append(
            {
                "bait": bait_id,
                "class": cls,
                "victim": victim,
                "margin": full[bait_id] - full[victim],
                "labelled_lever": target,
                "labelled_swing": swings[target],
                "dominant_lever": dominant,
                "dominant_swing": swings[dominant],
                "entangled": dominant != target
                and dominant in _entangled_with(target),
                "swings": swings,
            }
        )
    return rows
