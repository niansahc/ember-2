"""
tools/retrieval_trace/params.py

The parameter vector: every tunable constant in the retrieval and scoring
path, named, with its shipped default.

This is the surface a sensitivity pass perturbs. It is deliberately flat
and dotted rather than nested, because a Sobol or Morris design wants one
index per dimension and no structure to unpack.

Two kinds of default live here, and the difference is the point.

The retrieval, policy and authorship terms are still a SECOND copy of
numbers written inline in src/retrieval/semantic_search.py and
src/context/ranker.py. That is a real hazard -- a copy that drifts is worse
than no copy, because it fails quietly and every downstream sensitivity
number is then wrong about the system it claims to describe. So
tests/test_retrieval_trace.py pins each of those by calling the shipped
function and reading the value back out.

The prior and tier terms are IMPORTED rather than copied. Under ADR-044
their values are derived -- from the measured cosine spread and from Sobol
ST on the delivery endpoint -- rather than authored, so there is no
independent number for a test to pin them against; a pinning test would
just restate the derivation and pass by construction. Importing removes the
copy instead of guarding it, which is strictly better where it is available.
The pinning tests for those parameters were deleted rather than migrated,
because an identity is not a test.

Policy-scoped values (memory_weight, reflection_weight) are NOT in this
table. They vary per policy and are captured per query from the policy
object in effect; replay overrides them through
ReplayParams.policy_overrides. recency_bias was a third such value until
ADR-044 removed it from ContextPolicy: it scaled a second additive copy of
the recency ladder, and recency now reaches ranking once, inside the prior.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.context import prior as _prior
from src.context.ranker import COLD_MULTIPLIER, WARM_MULTIPLIER

# ---------------------------------------------------------------------------
# Retrieval stage -- src/retrieval/semantic_search.py
#
# ret.type.* and ret.quality.* stood here until ADR-044. semantic_search no
# longer calls memory_type_adjustment or source_quality_adjustment, so those
# twelve parameters had no call site to be sensitive at: sweeping them moved
# nothing, which would have read as twelve inert parameters rather than as a
# stale table. The type signal is in the prior; the role signal is a
# predicate (src/context/role_predicate.py) and therefore not a parameter at
# all.
# ---------------------------------------------------------------------------

RETRIEVAL_DEFAULTS: dict[str, float] = {
    # lexical_relevance_bonus
    "ret.lexical.substring": 0.10,
    "ret.lexical.term_hit": 0.03,
    "ret.lexical.term_cap": 0.18,
    "ret.lexical.entity_hit": 0.20,
    "ret.lexical.entity_cap": 0.40,
    # query_intent_adjustment
    "ret.intent.reflective_conversation": 0.10,
    "ret.intent.reflective_reflection": 0.08,
    "ret.intent.reflective_ingested": -0.03,
    "ret.intent.reflective_user_prefix": 0.10,
    "ret.intent.reflective_assistant_prefix": -0.10,
    "ret.intent.task_ingested": 0.08,
    "ret.intent.task_conversation": 0.03,
}

# ---------------------------------------------------------------------------
# Policy stage -- ContextRanker.apply_policy
# ---------------------------------------------------------------------------

POLICY_DEFAULTS: dict[str, float] = {
    "pol.prefer_experience": 0.20,
    "pol.prefer_active_work": 0.22,
    "pol.exact.question": -0.05,
    "pol.exact.other": 0.03,
    # ADR-015 tier multipliers, re-derived under ADR-044's bound and imported
    # rather than restated. hot is listed even though the shipped code
    # expresses it as "no change": a sensitivity pass needs the identity
    # element to be a dimension it can move, or hot is silently pinned.
    "tier.cold": COLD_MULTIPLIER,
    "tier.warm": WARM_MULTIPLIER,
    "tier.hot": 1.0,
    "tier.profile_bypass": 1.0,
}

# ---------------------------------------------------------------------------
# Authorship and project -- ContextRanker.apply_authorship_scoring /
# apply_project_boost
# ---------------------------------------------------------------------------

AUTHORSHIP_DEFAULTS: dict[str, float] = {
    "auth.first_person": 1.0,
    "auth.mixed": 0.3,
    "auth.third_party": 0.0,
    "auth.unknown": 0.5,
    "proj.boost": 0.15,
}

# ---------------------------------------------------------------------------
# Rank stage -- the bounded metadata prior, src/context/prior.py
#
# Seventeen additive rank.* terms and three refl.* terms stood here. ADR-044
# decision 2 replaced them with one multiplier, so the parameter vector
# shrinks to the factors that multiplier is built from. What went where:
#
#   rank.type.*        deleted. Every arm is in Sobol's
#                      no_solo_delivery_effect list, and it was one of the
#                      two terms counted twice.
#   rank.role.*        not a parameter any more. Role is a predicate.
#   rank.kind.*        below, as multipliers.
#   rank.len.lt20      deleted. The branch is gone; the prior has <50 and
#                      >1200 only.
#   rank.tokens_lt5    deleted. Subsumed by the length term it duplicated.
#   rank.user_prefix   deleted, with the rest of the content-prefix scoring.
#   refl.short,
#   refl.recency_scale deleted. Neither has a defensible magnitude.
#
# These are IMPORTED, not copied. See the module docstring.
# ---------------------------------------------------------------------------

PRIOR_DEFAULTS: dict[str, float] = {
    "prior.kind.experience": _prior.KIND_EXPERIENCE,
    "prior.kind.user_content": _prior.KIND_USER_CONTENT,
    "prior.kind.question": _prior.KIND_QUESTION,
    "prior.kind.answer": _prior.KIND_ANSWER,
    "prior.len.lt50": _prior.LEN_UNDER_50,
    "prior.len.gt1200": _prior.LEN_OVER_1200,
    "prior.reflection_discount": _prior.REFLECTION_DISCOUNT,
}

# ---------------------------------------------------------------------------
# Recency -- one table, now exactly one consumer.
#
# It had two: apply_policy scaled it by policy.recency_bias and
# _score_memory_item added it unscaled, which was two of the three counts
# #207 found. Both are gone; the buckets are multipliers inside the prior.
# ---------------------------------------------------------------------------

RECENCY_DEFAULTS: dict[str, float] = {
    f"prior.recency.{bucket}": value for bucket, value in _prior.RECENCY.items()
}


def default_params() -> dict[str, float]:
    """The full parameter vector at shipped values."""
    merged: dict[str, float] = {}
    for table in (
        RETRIEVAL_DEFAULTS,
        POLICY_DEFAULTS,
        AUTHORSHIP_DEFAULTS,
        PRIOR_DEFAULTS,
        RECENCY_DEFAULTS,
    ):
        overlap = merged.keys() & table.keys()
        if overlap:
            raise ValueError(f"duplicate parameter name(s): {sorted(overlap)}")
        merged.update(table)
    return merged


PARAM_NAMES: tuple[str, ...] = tuple(sorted(default_params()))


@dataclass
class ReplayParams:
    """A perturbed parameter vector for one replay.

    `values` starts from the shipped defaults; anything not overridden keeps
    its default, so a one-at-a-time sweep does not have to restate every
    number. `policy_overrides` reaches the per-policy weights, keyed by
    policy name then field, e.g. {"reflective": {"memory_weight": 0.9}}.
    """

    values: dict[str, float] = field(default_factory=default_params)
    policy_overrides: dict[str, dict[str, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        unknown = set(self.values) - set(PARAM_NAMES)
        if unknown:
            raise ValueError(f"unknown parameter name(s): {sorted(unknown)}")
        # Fill any name the caller left out, so lookups never KeyError
        # mid-composition and quietly turn a term into zero.
        for name, value in default_params().items():
            self.values.setdefault(name, value)

    def __getitem__(self, name: str) -> float:
        return self.values[name]

    def with_overrides(self, **overrides: float) -> "ReplayParams":
        merged = dict(self.values)
        merged.update(overrides)
        return ReplayParams(values=merged, policy_overrides=dict(self.policy_overrides))

    def policy_field(self, policy_name: str, field_name: str, captured: float) -> float:
        """The value to use for a policy-scoped weight.

        Falls back to what was captured, so an unperturbed replay uses the
        policy that was actually in effect rather than a reconstruction of
        it from the policy table -- the two can differ if policies are
        retuned between capture and analysis.
        """
        return self.policy_overrides.get(policy_name, {}).get(field_name, captured)
