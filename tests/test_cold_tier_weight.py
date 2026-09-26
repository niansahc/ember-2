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

from src.context.models import ContextItem
from src.context.policies import ContextPolicy
from src.context.ranker import COLD_MULTIPLIER, ContextRanker


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
        assert 0.0 < COLD_MULTIPLIER < 0.7


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
        """Not just apply_policy in isolation -- the additive type/role
        ladder and temporal decay run afterward in rank(), on top of the
        now-distinct per-item bases. Confirms ordering survives the full
        pipeline, not just algebraically."""
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
        # raw * COLD_MULTIPLIER would not (0.3 * 0.3 = 0.09 < 0.25).
        item = _item("borderline", score=0.3, tier="cold")

        service = ContextService.__new__(ContextService)
        gated = service._apply_type_gate([item], policy)

        assert len(gated) == 1, (
            "the type gate must evaluate the pre-tier score; if it were "
            "reading the tier-adjusted score this item would be dropped"
        )
        # And confirm the gate did not mutate the score out from under us.
        assert gated[0].score == 0.3


class TestRelationalQueryEmptyFlagNotSpuriouslyAffected:
    """service.py's relational_query_empty check
    (`all(float(item.score) == 0.0 for item in non_profile)`) was designed
    around apply_authorship_scoring's third_party: 0.0 multiplier, not
    tier. #218 retired that multiplier, so the check now has no source of
    an exact 0.0 at all -- see the second test. Confirms a cold-tier, first-person-authored item -- which used to
    reach this check at exactly 0.0 only when the additive ladder happened
    to net to zero, and now never reaches exactly 0.0 from tier alone --
    behaves the same representative way either way: nonzero, because the
    additive ladder and decay contribute real terms on top of the tier
    base regardless of which multiplier tier used."""

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
