"""
tests/test_composition_bound.py

ADR-044's bound, as a test rather than a comment.

The ADR is explicit that this is the requirement: "It is a test, not a
comment. One place computes the bound, one test asserts it, and the ratio to
the measured spread is stated in the code rather than implied." The failure it
names is already in the record -- the named-entity boost was sized against an
ASSUMED cosine variance of 0.3-0.5 and shipped with a cap of 0.40, which is
4.9x the measured production spread it was meant to nudge. The assumption was
wrong by a factor of four to six, and nothing failed when it was.

Two properties, and they are not the same claim.

THE BOUND is that `prior x tier` cannot leave [0.8722, 1.1278] for any
candidate. That is an upper limit on how far query-independent metadata may
move a record, expressed in cosine units so it can be compared against what
the embedder actually resolves.

REACHABILITY is what the bound buys. ADR-015's COLD_MULTIPLIER claimed that a
highly relevant cold record could outrank a weakly relevant hot one. That was
false as shipped -- an aged cold conversation record carried 0.3 x 0.10 = 0.03
against 1.0, and cosine is bounded by 1.0, so it held in 0 of 36 queries at
any cosine value. Under the bound it holds in 16 of 36: the queries whose
internal top-8 spread exceeds what tier now costs.

A bound test alone would pass on a contract that permits nothing, which is why
the second property is here. Fixtures are synthetic (CLAUDE.md Vault Privacy
Rule).
"""

from __future__ import annotations

import contextlib
import itertools

import pytest

from src.context import prior
from src.context.models import ContextItem
from src.context.policies import ContextPolicy
from src.context.ranker import COLD_MULTIPLIER, WARM_MULTIPLIER, ContextRanker

# Every branch prior.assemble can take, enumerated rather than sampled. The
# space is small enough to cover exhaustively, and a sampled bound test can
# miss exactly the corner where the clamp is load-bearing.
#
# The domains are READ OFF the prior's own term tables, not written out here.
# A hand-written copy would let a term added to _KIND_FACTORS or RECENCY leave
# this test passing AND still claiming exhaustiveness -- and exhaustiveness is
# the one property the count assertion below exists to protect. "unrecognised"
# and None are appended because they are inputs the tables deliberately do not
# carry, and the normalisers' handling of them is part of the contract.
CONTENT_KINDS = [None, *prior._KIND_FACTORS, "unrecognised"]
POLICY_ARMS = [*prior._POLICY_FACTORS, "unrecognised"]
PROJECT_MATCHES = [False, True]
CONTENT_LENGTHS = [
    0, 1,
    prior.SHORT_CHARS - 1, prior.SHORT_CHARS, prior.SHORT_CHARS + 1,
    600,
    prior.LONG_CHARS, prior.LONG_CHARS + 1, 5000,
]
RECENCY_BUCKETS = [*prior.RECENCY, "unrecognised"]
REFLECTION_FLAGS = [False, True]

# The tier multipliers as apply_policy applies them, including the identity
# arms. profile_bypass and hot are both 1.0 and are listed separately because
# they are different branches that could diverge.
TIER_MULTIPLIERS = {
    "cold": COLD_MULTIPLIER,
    "warm": WARM_MULTIPLIER,
    "hot": 1.0,
    "profile_bypass": 1.0,
}


@contextlib.contextmanager
def _out_of_contract_kind(kind: str, value: float):
    """Give one content_kind a magnitude the derivation would never produce.

    Used to prove the clamp is the enforcement rather than a formality. The
    derived magnitudes happen to compose within the bound today; that is not
    what makes the contract hold, and a test that only exercised them would
    pass just as well with no clamp at all.
    """
    original = dict(prior._KIND_FACTORS)
    prior._KIND_FACTORS[kind] = value
    try:
        yield
    finally:
        prior._KIND_FACTORS.clear()
        prior._KIND_FACTORS.update(original)


def _raw_product(content_kind, content_length: int, recency_bucket: str,
                 policy_arm: str = "none", project_match: bool = False,
                 is_reflection: bool = False) -> float:
    """The prior BEFORE the clamp, so the clamp's own firing can be measured."""
    arm = policy_arm if policy_arm in prior._POLICY_FACTORS else "none"
    value = (
        prior._KIND_FACTORS[prior.kind_branch(content_kind)]
        * prior._LENGTH_FACTORS[prior.length_branch(content_length)]
        * prior.RECENCY[prior.recency_bucket_or_unparsed(recency_bucket)]
        * prior._POLICY_FACTORS[arm]
        * prior._PROJECT_FACTORS["match" if project_match else "none"]
    )
    if is_reflection:
        value *= prior.REFLECTION_DISCOUNT
    return value


def _priors():
    """Every combination the pipeline can produce, across all five families."""
    for kind, length, bucket, is_reflection, arm, proj in itertools.product(
        CONTENT_KINDS, CONTENT_LENGTHS, RECENCY_BUCKETS, REFLECTION_FLAGS,
        POLICY_ARMS, PROJECT_MATCHES,
    ):
        yield (
            kind, length, bucket, is_reflection, arm, proj,
            prior.assemble(
                content_kind=kind,
                content_length=length,
                recency_bucket=bucket,
                is_reflection=is_reflection,
                policy_arm=arm,
                project_match=proj,
            ),
        )


class TestTheBoundItself:
    def test_the_interval_is_derived_from_the_measured_spread(self):
        """B = spread / mean rank-1 cosine, not a number someone liked.

        This is the assertion that makes the bound auditable: it ties the
        interval to the two measured quantities (#236) rather than to a
        constant that could drift away from them.

        Deliberately NOT a literal pin on 0.8722 / 1.1278. ADR-044 requires the
        spread be re-measured per embedder, so a literal here would fail on a
        legitimate re-measurement while asserting nothing about behaviour --
        and would be "fixed" by editing the number, which is the habit the
        whole contract exists to break. The relationship is what must hold.
        """
        assert prior.RELATIVE_SPREAD == pytest.approx(
            prior.COSINE_SPREAD / prior.COSINE_MEAN_RANK1
        )
        assert prior.COMPOSED_MIN == pytest.approx(1.0 - prior.RELATIVE_SPREAD)
        assert prior.COMPOSED_MAX == pytest.approx(1.0 + prior.RELATIVE_SPREAD)

    def test_the_budget_is_split_evenly_in_the_multiplicative_sense(self):
        """The worst case of both factors together lands ON the bound.

        That is the behavioural content of an even split, and the only form of
        it worth asserting: it is what makes the composed bound and the
        per-factor floors the same statement rather than two. An arithmetic
        split would leave the product below the floor.

        `PRIOR_MIN == COMPOSED_MIN ** 0.5` is NOT asserted -- it restates
        prior.py's own definition, and params.py argues in this same change
        that an identity is not a test.
        """
        assert prior.PRIOR_MIN * prior.TIER_MIN == pytest.approx(prior.COMPOSED_MIN)

    def test_tier_carries_no_upward_half(self):
        """The prior may promote; tier may only discount.

        Asymmetric on purpose: a record should not be promoted for being hot,
        only spared the cold discount. So tier's ceiling is 1.0 while the
        prior reaches COMPOSED_MAX, and the composed maximum is therefore the
        prior's maximum alone.
        """
        assert max(TIER_MULTIPLIERS.values()) == 1.0
        assert prior.PRIOR_MAX == prior.COMPOSED_MAX

    @pytest.mark.parametrize("tier_name", sorted(TIER_MULTIPLIERS))
    def test_prior_times_tier_stays_inside_the_bound(self, tier_name):
        """The contract, over every combination the pipeline can produce."""
        tier = TIER_MULTIPLIERS[tier_name]
        checked = 0
        for kind, length, bucket, is_reflection, arm, proj, value in _priors():
            composed = value * tier
            assert prior.COMPOSED_MIN - 1e-12 <= composed <= prior.COMPOSED_MAX + 1e-12, (
                f"prior x tier left the bound: kind={kind!r} length={length} "
                f"bucket={bucket!r} reflection={is_reflection} policy={arm!r} "
                f"project={proj} tier={tier_name} -> {composed}"
            )
            checked += 1
        assert checked == (
            len(CONTENT_KINDS) * len(CONTENT_LENGTHS) * len(RECENCY_BUCKETS)
            * len(REFLECTION_FLAGS) * len(POLICY_ARMS) * len(PROJECT_MATCHES)
        )

    def test_the_bound_is_reached_and_not_merely_respected(self):
        """Non-vacuity. A prior pinned at 1.0 would satisfy every test above.

        The floor must actually be attainable, or the contract is decorative
        and the reachability property below is unreachable for a different
        reason than the one it reports.
        """
        values = [v for *_rest, v in _priors()]
        assert min(values) == pytest.approx(prior.PRIOR_MIN, abs=1e-9), (
            "no combination reaches the prior's floor, so the bound is looser "
            "than the terms can use and the budget is being wasted"
        )
        assert max(values) == pytest.approx(prior.PRIOR_MAX, abs=1e-9), (
            "no combination reaches the prior's ceiling"
        )

    def test_a_term_cannot_escape_the_clamp(self):
        """The clamp is the enforcement, not the derivation.

        Derived magnitudes happen to compose within the bound today. That is
        not what makes the contract hold -- a future term, or a retune of an
        existing one, could push the product out. This asserts the clamp
        catches it, by handing assemble a deliberately out-of-contract term.
        """
        with _out_of_contract_kind("experience", 5.0):
            escaped = prior.assemble(
                content_kind="experience", content_length=600, recency_bucket="d7"
            )
        assert escaped == pytest.approx(prior.PRIOR_MAX)

    def test_the_clamp_catches_the_floor_side_too(self):
        with _out_of_contract_kind("experience", 0.01):
            escaped = prior.assemble(
                content_kind="experience", content_length=600, recency_bucket="d7"
            )
        assert escaped == pytest.approx(prior.PRIOR_MIN)

    def test_the_clamp_is_unreachable_at_shipped_magnitudes(self):
        """The clamp cannot fire. That is the point of the log-space allocation.

        This test used to assert the OPPOSITE, and its own failure message said
        to delete it if the derivation was ever fixed to compose within the
        bound. It has been, so the assertion is inverted rather than removed --
        the rate going from 9-of-90 to zero is the result, and a test that only
        described the old state would have left nothing watching the new one.

        What changed: the budget was allocated per term additively
        (`deviation_i = (1 - PRIOR_MIN) * ST_i / ST_max`) while `assemble`
        composes by multiplying, so the largest-ST term consumed the whole
        budget alone and any second downward term left the bound. Allocating in
        log space across the mutually-exclusive families makes the worst case
        land exactly on the bound instead.

        Checked on the RAW product, before the clamp, because checking the
        clamped value would be circular -- it is clamped by definition.
        """
        escaped = [
            (kind, length, bucket, arm, proj, raw)
            for kind, length, bucket, is_reflection, arm, proj, _value in _priors()
            if not (
                prior.PRIOR_MIN - 1e-9
                <= (raw := _raw_product(kind, length, bucket, arm, proj, is_reflection))
                <= prior.PRIOR_MAX + 1e-9
            )
        ]
        assert not escaped, (
            f"{len(escaped)} combination(s) leave the bound before the clamp, so "
            f"the clamp is still doing enforcement rather than assertion. First: "
            f"{escaped[0]}"
        )

    def test_the_worst_case_per_family_lands_exactly_on_the_bound(self):
        """Tight, not merely inside -- which is what makes the clamp unreachable.

        A conservative allocation would also satisfy the test above while
        wasting budget: every term would be smaller than it is entitled to and
        the prior would have less authority than the contract permits. So the
        worst one-arm-per-family product has to EQUAL the bound, not just
        respect it.

        This is also the assertion that breaks if someone adds a sixth family
        without re-deriving: the new family's arm would push the worst case past
        the bound, and the equality fails before the clamp has to catch it.
        """
        worst_down = (
            prior.KIND_QUESTION
            * prior.LEN_UNDER_50
            * prior.RECENCY["older"]
            * prior.POL_EXACT_QUESTION
            * prior.REFLECTION_DISCOUNT
        )
        worst_up = (
            prior.KIND_EXPERIENCE
            * prior.RECENCY["d7"]
            * prior.POL_PREFER_ACTIVE_WORK
            * prior.PROJECT_MATCH
        )
        assert worst_down == pytest.approx(prior.PRIOR_MIN, abs=1e-9)
        assert worst_up == pytest.approx(prior.PRIOR_MAX, abs=1e-9)

    def test_the_recency_ladder_is_monotonic(self):
        """Fresher must never score below staler.

        The one place in the prior where measurement cannot set the ordering.
        Recency arms are mutually exclusive, so they are compared ACROSS
        records; allocating each by its own ST would invert the ladder, because
        measured d365 (0.0903) is 8.2x measured d30 (0.0111) on the production
        corpus. ST is an activation-weighted variance share -- it says how much
        a term moves delivery, not which arm of an ordinal ladder ranks higher.

        So the ladder's ordering is carried and its magnitude measured, and this
        asserts the ordering survived.
        """
        r = prior.RECENCY
        assert r["d7"] > r["d30"] > r["d90"] > r["d365"] > 1.0 > r["older"]
        assert r["unparsed"] == 1.0

    def test_the_policy_preference_flags_are_mutually_exclusive(self):
        """The prior treats policy preference as ONE family. Verify the premise.

        The worst-case allocation sums one arm per family, so if any policy set
        two preference flags the budget would be under-allocated and the bound
        could be exceeded by a combination the cross product above never builds
        -- because prior.policy_branch returns a single arm by construction and
        would hide it.
        """
        from src.context import policies as P

        flags = ("prefer_experiences", "prefer_active_work", "prefer_exact_matches")
        offenders = []
        # Policies are constructed inside classify_query rather than exported,
        # so they are reached through it -- one query per policy that sets a
        # flag, plus a few that set none.
        for query in (
            "what am i working on", "what are my open loops",
            "status of the current project", "what patterns have you noticed lately",
            "find what i said about the retrieval design", "what was on my mind lately",
            "am i building anything worthwhile", "tell me something useful",
            "what do you know about me",
        ):
            policy = P.classify_query(query)
            enabled = [f for f in flags if getattr(policy, f, False)]
            if len(enabled) > 1:
                offenders.append((policy.name, enabled))
        assert not offenders, (
            f"a policy sets more than one preference flag: {offenders}. The "
            "prior allocates them as one mutually-exclusive family."
        )

    def test_the_clamp_rate_is_observable(self):
        """The counters that surface the finding above must exist.

        prior.clamped_low / prior.clamped_high were added with the instrument
        and then appeared in no traffic-window target, so the mechanism
        designed to surface a saturating budget would not have surfaced it.
        """
        import sys
        from pathlib import Path

        tools = str(Path(__file__).resolve().parents[1] / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        import guard_counter_sites as sites
        import traffic_window as window

        declared = {row["site"] for row in sites.declared_sites()}
        assert "prior.clamped_low" in declared
        assert "prior.clamped_high" in declared

        targeted = {t for entry in window.QUERY_SET for t in entry.get("targets", ())}
        assert "prior.clamped_low" in targeted, (
            "no query in the traffic window targets the clamp; a saturating "
            "budget would not show up in the window that exists to show it"
        )


class TestReachability:
    """A rank-1 cold record outranking a rank-8 hot record.

    The property ADR-015's COLD_MULTIPLIER rationale claimed and did not
    deliver. It is arithmetic rather than empirical: the cold record finishes
    at c1 x COMPOSED_MIN in the worst case, the hot one at c8 x 1.0, so it
    holds exactly when

        (c1 - c8) / c1 > RELATIVE_SPREAD        (0.12784)

    equivalently when the cold record's cosine ADVANTAGE, c1/c8 - 1, exceeds
    1/COMPOSED_MIN - 1 = 14.7%.

    Baseline: 0 of 36. Under 0.3 x 0.10 = 0.03 an aged cold conversation
    record could not outrank a fresh hot one at ANY cosine value, because
    cosine is bounded by 1.0. Absorbing the temporal decay lifted the floor
    from 0.03 to 0.30 and left it at 0 of 36. Under the bound: 16 of 36.
    """

    def setup_method(self):
        self.ranker = ContextRanker()
        self.policy = ContextPolicy(name="test", memory_weight=1.0)

    def _finish(self, cosine: float, tier: str, content: str, kind: str | None) -> float:
        item = ContextItem(
            id=f"{tier}-{cosine}",
            content=content,
            source="conversation",
            item_type="conversation",
            memory_type="conversation",
            score=cosine,
            tier=tier,
            timestamp=None,
            metadata={"content_kind": kind} if kind else {},
        )
        self.ranker.apply_policy([item], self.policy)
        ranked, _ = self.ranker.rank([item], [])
        return float(ranked[0].score)

    def test_the_required_advantage_is_the_measured_relative_spread(self):
        required = 1.0 / prior.COMPOSED_MIN - 1.0
        assert required == pytest.approx(0.1466, abs=5e-5)
        # The same condition stated against rank-1 rather than as a ratio.
        assert 1.0 - prior.COMPOSED_MIN == pytest.approx(prior.RELATIVE_SPREAD)

    def test_a_rank_1_cold_record_outranks_a_rank_8_hot_record(self):
        """The demonstration, at the worst case for the cold record.

        Cold takes the prior's floor (short content, stale) and hot takes the
        neutral prior, so the cold record is carrying the entire composed
        discount. A spread above the threshold still leaves it on top.
        """
        short = "a short record"                       # < 50 chars: LEN_UNDER_50
        neutral = "a record body of a perfectly ordinary and unremarkable length"

        c1, c8 = 0.6375, 0.5200                        # relative spread 0.1843
        assert (c1 - c8) / c1 > prior.RELATIVE_SPREAD

        cold = self._finish(c1, "cold", short, "question")
        hot = self._finish(c8, "hot", neutral, None)

        assert cold > hot, (
            f"a rank-1 cold record at cosine {c1} lost to a rank-8 hot record "
            f"at {c8}, on a spread that clears the threshold: {cold} vs {hot}"
        )

    def test_below_the_threshold_the_cold_record_loses(self):
        """The negative case, so the test pins the THRESHOLD, not an outcome.

        Without this, the test above would pass for a contract that let cold
        records win everywhere, which is the opposite failure and equally bad.
        """
        short = "a short record"
        neutral = "a record body of a perfectly ordinary and unremarkable length"

        c1, c8 = 0.6375, 0.6000                        # relative spread 0.0588
        assert (c1 - c8) / c1 < prior.RELATIVE_SPREAD

        cold = self._finish(c1, "cold", short, "question")
        hot = self._finish(c8, "hot", neutral, None)

        assert cold < hot, (
            "a cold record won on a spread below the threshold; tier is not "
            "costing what the contract says it costs"
        )

    def test_the_old_multipliers_could_not_deliver_this_at_any_cosine(self):
        """The 0/36 baseline, as arithmetic rather than as a citation.

        0.3 (tier) x 0.10 (the ephemeral decay floor) = 0.03. Cosine is
        bounded by 1.0, so the best possible aged cold record finished at 0.03
        against a fresh hot record's cosine -- unreachable for every corpus,
        not merely for this one.
        """
        old_cold, old_decay_floor = 0.3, 0.10
        best_possible_cold = 1.0 * old_cold * old_decay_floor

        # The claim that matters is about LIVE code: the current floor leaves a
        # cold record above the measured cosine spread, where the old one did
        # not. Asserting 0.3 * 0.10 == 0.03 on its own would be arithmetic over
        # deleted constants, which cannot fail for any change to the system.
        assert best_possible_cold < prior.COSINE_SPREAD
        assert prior.COMPOSED_MIN > prior.COSINE_SPREAD
