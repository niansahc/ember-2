"""
tools/retrieval_trace/params.py

The parameter vector: every tunable constant in the retrieval and scoring
path, named, with its shipped default.

This is the surface a sensitivity pass perturbs. It is deliberately flat
and dotted rather than nested, because a Sobol or Morris design wants one
index per dimension and no structure to unpack.

The defaults here are a SECOND copy of numbers that live inline in
src/retrieval/semantic_search.py and src/context/ranker.py, which is a real
hazard: a copy that drifts is worse than no copy, because it fails quietly
and every downstream sensitivity number is then wrong about the system it
claims to describe. tests/test_retrieval_trace.py pins each default by
calling the shipped function and reading the value back out, so a retune in
src/ fails the suite here rather than silently biasing an analysis.

Policy-scoped values (memory_weight, reflection_weight, recency_bias) are
NOT in this table. They vary per policy and are captured per query from the
policy object in effect; replay overrides them through
ReplayParams.policy_overrides.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Retrieval stage -- src/retrieval/semantic_search.py
# ---------------------------------------------------------------------------

RETRIEVAL_DEFAULTS: dict[str, float] = {
    # lexical_relevance_bonus
    "ret.lexical.substring": 0.10,
    "ret.lexical.term_hit": 0.03,
    "ret.lexical.term_cap": 0.18,
    "ret.lexical.entity_hit": 0.20,
    "ret.lexical.entity_cap": 0.40,
    # memory_type_adjustment
    "ret.type.conversation": 0.10,
    "ret.type.reflection": 0.05,
    "ret.type.memory": 0.03,
    "ret.type.ingested": -0.02,
    "ret.type.other": 0.0,
    # source_quality_adjustment
    "ret.quality.role_user": 0.16,
    "ret.quality.role_assistant": -0.20,
    "ret.quality.question": -0.10,
    "ret.quality.not_question": 0.04,
    "ret.quality.clarification": -0.12,
    "ret.quality.experience": 0.10,
    "ret.quality.summary": -0.14,
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
    # ADR-015 tier multipliers. hot is listed even though the shipped code
    # expresses it as "no change": a sensitivity pass needs the identity
    # element to be a dimension it can move, or hot is silently pinned.
    "tier.cold": 0.3,
    "tier.warm": 0.7,
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
# Rank stage -- ContextRanker._score_memory_item / _score_reflection_item
# ---------------------------------------------------------------------------

RANK_DEFAULTS: dict[str, float] = {
    "rank.type.conversation": 0.10,
    "rank.type.reflection": 0.06,
    "rank.type.memory": 0.04,
    "rank.type.ingested": 0.0,
    "rank.type.other": 0.0,
    "rank.role.user": 0.12,
    "rank.role.assistant": -0.25,
    "rank.role.tool_system": -0.20,
    "rank.kind.experience": 0.14,
    "rank.kind.user_content": 0.05,
    "rank.kind.answer": -0.10,
    "rank.kind.question": -0.10,
    "rank.user_prefix": 0.04,
    "rank.len.lt20": -0.10,
    "rank.len.lt50": -0.04,
    "rank.len.gt1200": -0.03,
    "rank.tokens_lt5": -0.05,
    # _score_reflection_item
    "refl.base_discount": 0.95,
    "refl.short": -0.08,
    "refl.recency_scale": 0.5,
}

# ---------------------------------------------------------------------------
# Recency -- ContextRanker._recency_boost
#
# One table, two consumers: apply_policy scales it by policy.recency_bias,
# _score_memory_item adds it unscaled. Both read the same buckets, so they
# are one set of parameters and a sensitivity pass must move them together
# or it is measuring a system that does not exist.
# ---------------------------------------------------------------------------

RECENCY_DEFAULTS: dict[str, float] = {
    "recency.d7": 0.18,
    "recency.d30": 0.12,
    "recency.d90": 0.06,
    "recency.d365": 0.02,
    "recency.older": -0.03,
    "recency.unparsed": 0.0,
}

# ---------------------------------------------------------------------------
# Temporal decay -- ContextRanker._temporal_decay_weight
# ---------------------------------------------------------------------------

DECAY_DEFAULTS: dict[str, float] = {
    "decay.none": 1.0,
    "decay.reflection.d7": 1.0,
    "decay.reflection.d30": 0.80,
    "decay.reflection.d90": 0.60,
    "decay.reflection.older": 0.40,
    "decay.ephemeral.d3": 1.0,
    "decay.ephemeral.d7": 0.70,
    "decay.ephemeral.d14": 0.45,
    "decay.ephemeral.d30": 0.25,
    "decay.ephemeral.older": 0.10,
    "decay.default.d3": 1.0,
    "decay.default.d7": 0.85,
    "decay.default.d14": 0.70,
    "decay.default.d30": 0.50,
    "decay.default.d90": 0.30,
    "decay.default.older": 0.15,
}


def default_params() -> dict[str, float]:
    """The full parameter vector at shipped values."""
    merged: dict[str, float] = {}
    for table in (
        RETRIEVAL_DEFAULTS,
        POLICY_DEFAULTS,
        AUTHORSHIP_DEFAULTS,
        RANK_DEFAULTS,
        RECENCY_DEFAULTS,
        DECAY_DEFAULTS,
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
    its default, so a one-at-a-time sweep does not have to restate 55
    numbers. `policy_overrides` reaches the per-policy weights, keyed by
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
