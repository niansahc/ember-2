"""
tests/test_cold_tier_weight.py

ADR-015 amendment (PR #180), implementation step 3: cold is a reduced
weight, not exclusion. Covers the behavioral claims from the amendment and
the downstream-interaction findings from planning: ordering preserved
within cold, the tie-band measurement improves, the type gate has zero
interaction with the tier multiplier (it runs before apply_policy), and
the relational_query_empty score==0.0 sentinel is not spuriously affected.
"""

from __future__ import annotations

import pytest

from src.context import prior
from src.context.models import ContextItem
from src.context.policies import ContextPolicy
from src.context.ranker import COLD_MULTIPLIER, WARM_MULTIPLIER, ContextRanker


def _item(item_id: str, score: float, tier: str = "cold", memory_type: str = "conversation") -> ContextItem:
    return ContextItem(
        id=item_id,
        content="content long enough to survive the low-value filter easily",
        source=memory_type,
        item_type=memory_type,
        memory_type=memory_type,
        score=score,
        tier=tier,
    )


class TestColdMultiplierValue:
    def test_cold_multiplier_is_nonzero_and_less_than_warm(self):
        """The ordering, stated without copying warm's value.

        The upper bound used to be the literal 0.7, which was a copy of the
        then-current WARM_MULTIPLIER. ADR-044 re-derived both under the bound
        (0.9339 / 0.9664) and the literal went stale, which is what a copied
        constant does. The test's own name says "less than warm", so compare
        against warm.
        """
        assert 0.0 < COLD_MULTIPLIER < WARM_MULTIPLIER < 1.0

    def test_cold_multiplier_is_the_contract_floor(self):
        """Cold takes tier's whole half of the bound, by construction."""
        assert COLD_MULTIPLIER == prior.TIER_MIN

    def test_warm_is_the_geometric_midpoint(self):
        """Warm sits between cold and hot multiplicatively, not additively.

        An arithmetic midpoint would be 0.967, which is close enough to look
        right and wrong for the reason the whole contract is multiplicative:
        the three tiers are points on a ratio scale, so the step from cold to
        warm and from warm to hot should be the same RATIO, not the same
        difference.
        """
        assert WARM_MULTIPLIER == pytest.approx(COLD_MULTIPLIER ** 0.5)
        assert WARM_MULTIPLIER / COLD_MULTIPLIER == pytest.approx(
            1.0 / WARM_MULTIPLIER
        )


class TestOrderingPreservedWithinCold:
    def test_more_relevant_cold_item_outranks_less_relevant_cold_item(self):
        """The task's explicit case: a cold record outranks another cold
        record when more relevant."""
        ranker = ContextRanker()
        policy = ContextPolicy(name="test", memory_weight=1.0)

        items = [
            _item("weak", score=0.3, tier="cold"),
            _item("strong", score=0.8, tier="cold"),
        ]
        result = ranker.apply_policy(items, policy)
        by_id = {i.id: i.score for i in result}

        assert by_id["strong"] > by_id["weak"]
        assert by_id["strong"] != by_id["weak"]

    def test_end_to_end_through_rank_preserves_order(self):
        """Not just apply_policy in isolation -- the bounded prior multiplies
        afterward in rank(), on top of the now-distinct per-item bases.
        Confirms ordering survives the full pipeline, not just algebraically.

        Stronger than it was: the additive ladder this used to run through
        could reorder two items outright, so order preservation was a real
        risk. A strictly positive multiplier applied uniformly cannot, which
        means the remaining risk is the prior varying BETWEEN the two items --
        which it does, on length and content_kind."""
        ranker = ContextRanker()
        policy = ContextPolicy(name="test", memory_weight=1.0)

        items = [
            _item("weak", score=0.3, tier="cold"),
            _item("strong", score=0.8, tier="cold"),
        ]
        adjusted = ranker.apply_policy(items, policy)
        ranked_memory, _ = ranker.rank(adjusted, [])

        assert [i.id for i in ranked_memory] == ["strong", "weak"]

    def test_all_cold_items_no_longer_tie(self):
        """The direct fix for the measured tie-band bloat: under the old
        score=0.0 behavior every cold item landed in one band. Under the
        multiplier, distinct base scores stay distinct."""
        ranker = ContextRanker()
        policy = ContextPolicy(name="test", memory_weight=1.0)

        items = [_item(f"c{i}", score=0.2 + i * 0.05, tier="cold") for i in range(5)]
        result = ranker.apply_policy(items, policy)
        scores = [i.score for i in result]

        assert len(set(scores)) == len(scores), "cold items collapsed onto shared scores"


class TestTieBandMeasurementImproves:
    def test_unresolved_fraction_drops_after_the_multiplier_change(self):
        """Reuses the exact metric the amendment cites (tools/retrieval_
        ablation/metrics.py's unresolved_fraction) rather than
        reimplementing tie-band logic, over a small synthetic cold-heavy
        set. Fast unit-level regression pin; a full ablation subprocess
        rerun is a natural follow-up, not required here."""
        from tools.retrieval_ablation.metrics import Delivered, unresolved_fraction

        base_scores = [0.72, 0.61, 0.55, 0.48, 0.33, 0.20]

        # Old behavior: apply_policy set every cold item to exactly 0.0.
        before = [Delivered(id=f"i{i}", score=0.0) for i in range(len(base_scores))]

        # New behavior: apply_policy multiplies by COLD_MULTIPLIER.
        after = [
            Delivered(id=f"i{i}", score=s * COLD_MULTIPLIER)
            for i, s in enumerate(base_scores)
        ]

        before_fraction = unresolved_fraction(before)
        after_fraction = unresolved_fraction(after)

        assert before_fraction == 1.0, "sanity: all-zero set should be one fully unresolved band"
        assert after_fraction == 0.0
        assert after_fraction < before_fraction


class TestTypeGateHasNoInteractionWithTierMultiplier:
    def test_gate_evaluates_score_before_apply_policy_runs(self):
        """_apply_type_gate runs BEFORE apply_policy in build_context, so it
        always sees the pre-tiering score. An item whose raw score clears
        min_score, and whose tier-adjusted score would NOT, must still pass
        the gate -- proving the gate is genuinely unaffected by the cold
        multiplier change, not just unaffected by coincidence."""
        from src.context.service import ContextService

        policy = ContextPolicy(name="test", memory_weight=1.0)
        # policies.py default min_score is 0.25. Raw score clears it;
        # raw * COLD_MULTIPLIER would not (0.26 * 0.9339 = 0.2428 < 0.25).
        #
        # The fixture was 0.3, chosen against the old 0.3 multiplier
        # (0.3 * 0.3 = 0.09). Under 0.9339 that becomes 0.28, which clears the
        # floor -- so the test still passed while no longer demonstrating
        # anything: a gate reading the tier-adjusted score would have kept the
        # item too. 0.26 is the value that makes the two readings differ again.
        item = _item("borderline", score=0.26, tier="cold")

        service = ContextService.__new__(ContextService)
        gated = service._apply_type_gate([item], policy)

        assert len(gated) == 1, (
            "the type gate must evaluate the pre-tier score; if it were "
            "reading the tier-adjusted score this item would be dropped"
        )
        # And confirm the gate did not mutate the score out from under us.
        assert gated[0].score == 0.26
        # Non-vacuity: the tier-adjusted score really is below the floor, so
        # the assertion above distinguishes the two readings.
        assert 0.26 * COLD_MULTIPLIER < policy.min_score


class TestRelationalQueryEmptyFlagNotSpuriouslyAffected:
    """service.py's relational_query_empty check
    (`all(float(item.score) == 0.0 for item in non_profile)`) was designed
    around apply_authorship_scoring's third_party: 0.0 multiplier, not
    tier. Confirms a cold-tier, first-person-authored item stays nonzero.

    TWO changes have since removed every source of an exact 0.0 here, and they
    arrived independently:

    #218 retired the `third_party` authorship class, which was the 0.0
    multiplier this check was built around -- see the second test.

    ADR-044 then made the reasoning structural rather than arithmetic. It used
    to be that the additive ladder and decay contributed real terms on top of
    the tier base, so the sum happened not to be zero. Now every stage after
    the retrieval score is a strictly positive multiplier, so a nonzero input
    cannot reach zero at all.

    So the signal has no live trigger. That is worth stating plainly rather
    than leaving these tests to assert the absence of something nothing can
    produce: the `relational_query_empty` flag now fires only via
    `zero_hit_signal.profile_only`, and whether the score-based half should
    remain is a question this file cannot answer."""

    def test_cold_first_person_item_is_not_spuriously_zero_after_full_pipeline(self):
        ranker = ContextRanker()
        policy = ContextPolicy(name="test", memory_weight=1.0)

        item = _item("cold-first-person", score=0.5, tier="cold")
        item.authorship = "first_person"
        item.timestamp = "2026-09-01T00-00-00"

        adjusted = ranker.apply_policy([item], policy)
        authored = ranker.apply_authorship_scoring(adjusted, "what has my son been up to")
        ranked_memory, _ = ranker.rank(authored, [])

        assert ranked_memory[0].score != 0.0

    def test_nothing_zeroes_on_a_relational_query_since_218(self):
        """The flag's only trigger is gone.

        It was third_party authorship at multiplier 0.0, a class with zero
        rows in production and no live path to acquire any, retired in
        #218. Tier never drove this check and still does not. So
        relational_query_empty can now only be reached through its
        profile_only arm, and the all_non_profile_zeroed arm is
        unreachable -- consistent with it firing 0 times in 5 evaluations
        on the personal-vault window before the retirement.

        Asserted rather than left implicit so that restoring any 0.0
        multiplier flips this test and forces the signal to be
        reconsidered with it.
        """
        ranker = ContextRanker()
        policy = ContextPolicy(name="test", memory_weight=1.0)

        item = _item("cold-ingested", score=0.5, tier="cold", memory_type="ingested")
        item.authorship = "third_party"  # the retired tag, if it somehow appears

        adjusted = ranker.apply_policy([item], policy)
        authored = ranker.apply_authorship_scoring(adjusted, "what has my son been up to")

        assert authored[0].score > 0.0


class TestProfileBypassStillWorks:
    def test_profile_cold_item_unaffected_by_cold_multiplier(self):
        ranker = ContextRanker()
        policy = ContextPolicy(name="test", memory_weight=1.0)

        item = _item("profile-1", score=0.5, tier="cold", memory_type="profile")
        result = ranker.apply_policy([item], policy)

        assert result[0].score == 0.5
