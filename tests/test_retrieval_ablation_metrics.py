"""
tests/test_retrieval_ablation_metrics.py

The ablation's instrument must not move with the treatment, and must not
report movement that is really tie order. Both properties are pinned here,
against hand-computed values rather than against the implementation.
"""

from __future__ import annotations

import math

import pytest

from tools.retrieval_ablation.metrics import (
    DEFAULT_TIE_EPSILON,
    Delivered,
    QueryLabels,
    abstention_correct,
    band_sizes,
    boundary_tie_count,
    composition,
    compare_to_reference,
    contamination_at_k,
    dcg,
    evaluate_query,
    ideal_dcg,
    leakage_by_class,
    mean_ignoring_none,
    mean_pairwise_jaccard,
    ndcg_at_k,
    rank_displacement,
    selection_jaccard,
    tie_band_indices,
    unresolved_fraction,
)


def _d(item_id: str, score: float, memory_type: str = "conversation", tier: str = "hot"):
    return Delivered(id=item_id, score=score, memory_type=memory_type, tier=tier)


class TestDCG:
    def test_dcg_matches_hand_computation(self):
        # gains 2^g - 1 = [7, 1, 0]; discounts log2(2), log2(3), log2(4)
        expected = 7 / 1.0 + 1 / math.log2(3) + 0 / 2.0
        assert dcg([3, 1, 0]) == pytest.approx(expected)

    def test_dcg_of_nothing_is_zero(self):
        assert dcg([]) == 0.0

    def test_grade_three_outweighs_three_grade_ones(self):
        """Exponential gain is deliberate: the metric must reward putting the
        BEST item first, not merely a relevant one."""
        assert dcg([3]) > dcg([1, 1, 1])

    def test_ideal_dcg_uses_the_whole_labelled_pool(self):
        labels = QueryLabels(grades={"a": 1, "b": 3, "c": 2, "d": 0})
        # best-first ordering at k=2 is [3, 2]
        assert ideal_dcg(labels, 2) == pytest.approx(7 / 1.0 + 3 / math.log2(3))


class TestNDCG:
    def test_perfect_order_scores_one(self):
        labels = QueryLabels(grades={"a": 3, "b": 2, "c": 1})
        delivered = [_d("a", 0.9), _d("b", 0.8), _d("c", 0.7)]
        assert ndcg_at_k(delivered, labels, 6) == pytest.approx(1.0)

    def test_reversed_order_scores_below_one(self):
        labels = QueryLabels(grades={"a": 3, "b": 2, "c": 1})
        perfect = [_d("a", 0.9), _d("b", 0.8), _d("c", 0.7)]
        reversed_ = [_d("c", 0.9), _d("b", 0.8), _d("a", 0.7)]
        assert ndcg_at_k(reversed_, labels, 6) < ndcg_at_k(perfect, labels, 6)

    def test_unlabelled_items_count_as_grade_zero(self):
        labels = QueryLabels(grades={"a": 3})
        delivered = [_d("junk", 0.9), _d("a", 0.1)]
        # gain 0 first, 7 second -> 7/log2(3) over ideal 7
        assert ndcg_at_k(delivered, labels, 6) == pytest.approx((7 / math.log2(3)) / 7.0)

    def test_is_none_when_nothing_relevant_exists(self):
        """Abstain strata: ranking quality is undefined with nothing to rank.
        None keeps them out of the mean instead of biasing it."""
        labels = QueryLabels(grades={"a": 0, "b": 0}, abstain=True)
        assert ndcg_at_k([_d("a", 0.5)], labels, 6) is None

    def test_empty_delivery_scores_zero_when_relevant_items_existed(self):
        labels = QueryLabels(grades={"a": 3})
        assert ndcg_at_k([], labels, 6) == pytest.approx(0.0)


class TestTreatmentIndependence:
    """The instrument must not read the treatment.

    These are the two failure modes that ruled out reusing score_result.
    """

    def test_uniform_score_shift_does_not_change_any_metric(self):
        """An arm that removes the tier multiplier raises every score. The old
        `score_above_threshold` criterion rewarded that; nothing here may."""
        labels = QueryLabels(grades={"a": 3, "b": 1})
        low = [_d("a", 0.10), _d("b", 0.05)]
        high = [_d("a", 0.98), _d("b", 0.93)]

        assert ndcg_at_k(low, labels, 6) == ndcg_at_k(high, labels, 6)
        assert contamination_at_k(low, labels) == contamination_at_k(high, labels)
        assert selection_jaccard(low, high) == 1.0

    def test_missing_memory_type_does_not_change_ranking_metrics(self):
        """`memory_type_present` was undefined under type-off. Ranking quality
        here must not depend on the type field being populated at all."""
        labels = QueryLabels(grades={"a": 3, "b": 1})
        typed = [_d("a", 0.9, memory_type="conversation"), _d("b", 0.8, memory_type="journal")]
        untyped = [_d("a", 0.9, memory_type=""), _d("b", 0.8, memory_type="")]

        assert ndcg_at_k(typed, labels, 6) == ndcg_at_k(untyped, labels, 6)
        assert contamination_at_k(typed, labels) == contamination_at_k(untyped, labels)


class TestTieBands:
    def test_equal_scores_share_one_band(self):
        delivered = [_d("a", 0.0), _d("b", 0.0), _d("c", 0.0)]
        assert tie_band_indices(delivered) == [0, 0, 0]

    def test_distinct_scores_get_distinct_bands(self):
        delivered = [_d("a", 0.9), _d("b", 0.5), _d("c", 0.1)]
        assert tie_band_indices(delivered) == [0, 1, 2]

    def test_band_sizes_and_unresolved_fraction(self):
        delivered = [_d("a", 0.9), _d("b", 0.0), _d("c", 0.0), _d("d", 0.0)]
        assert band_sizes(delivered) == {0: 1, 1: 3}
        assert unresolved_fraction(delivered) == pytest.approx(0.75)

    def test_all_cold_set_is_fully_unresolved(self):
        """apply_policy assigns cold `score = 0.0` exactly, so a cold-heavy
        arm delivers one big band. That must read as unresolved, not as
        agreement."""
        delivered = [_d(x, 0.0) for x in ("a", "b", "c", "d")]
        assert unresolved_fraction(delivered) == 1.0

    def test_boundary_ties_are_reported_at_the_cut(self):
        # positions 3,4,5 tie across the k=4 boundary
        delivered = [_d("a", 0.9), _d("b", 0.8), _d("c", 0.5), _d("d", 0.5), _d("e", 0.5)]
        assert boundary_tie_count(delivered, 4) == 3

    def test_no_boundary_ties_when_the_cut_is_clean(self):
        delivered = [_d("a", 0.9), _d("b", 0.8), _d("c", 0.7), _d("d", 0.6), _d("e", 0.1)]
        assert boundary_tie_count(delivered, 4) == 0


class TestDisplacementIgnoresTieOrder:
    """The non-vacuousness check for tie bands.

    Cold sets score exactly 0.0 and a stable sort preserves insertion order, so
    a position-based displacement metric would report movement produced purely
    by tie order. These prove it does not -- and that real movement still
    registers, so the bands are not simply swallowing everything.
    """

    def test_pure_tie_reordering_registers_zero_displacement(self):
        a = [_d("x", 0.0), _d("y", 0.0), _d("z", 0.0)]
        b = [_d("z", 0.0), _d("x", 0.0), _d("y", 0.0)]
        assert rank_displacement(a, b) == pytest.approx(0.0)

    def test_pure_tie_reordering_leaves_jaccard_at_one(self):
        a = [_d("x", 0.0), _d("y", 0.0), _d("z", 0.0)]
        b = [_d("z", 0.0), _d("x", 0.0), _d("y", 0.0)]
        assert selection_jaccard(a, b) == 1.0

    def test_real_movement_still_registers(self):
        """Guards against the bands absorbing genuine reordering."""
        a = [_d("x", 0.9), _d("y", 0.5), _d("z", 0.1)]
        b = [_d("z", 0.9), _d("y", 0.5), _d("x", 0.1)]
        assert rank_displacement(a, b) == pytest.approx(4 / 3)

    def test_is_none_when_arms_share_nothing(self):
        a = [_d("x", 0.9)]
        b = [_d("q", 0.9)]
        assert rank_displacement(a, b) is None


class TestContamination:
    def test_counts_grade_zero_and_distractors(self):
        labels = QueryLabels(
            grades={"good": 3, "meh": 1},
            distractor_classes={"bait": "decay_bait"},
        )
        delivered = [_d("good", 0.9), _d("bait", 0.8), _d("junk", 0.7), _d("meh", 0.6)]
        assert contamination_at_k(delivered, labels, 4) == pytest.approx(0.5)

    def test_clean_window_is_zero(self):
        labels = QueryLabels(grades={"a": 3, "b": 2})
        assert contamination_at_k([_d("a", 0.9), _d("b", 0.8)], labels, 4) == 0.0

    def test_empty_delivery_is_zero_not_one(self):
        """Delivering nothing is not contamination. Abstention correctness is
        the metric that judges an empty delivery."""
        assert contamination_at_k([], QueryLabels(grades={"a": 3})) == 0.0

    def test_defined_even_when_ndcg_is_not(self):
        labels = QueryLabels(grades={}, abstain=True)
        delivered = [_d("junk", 0.5)]
        assert ndcg_at_k(delivered, labels, 6) is None
        assert contamination_at_k(delivered, labels) == 1.0


class TestAbstentionAndLeakage:
    def test_correct_abstention_when_only_grade_zero_delivered(self):
        labels = QueryLabels(grades={"a": 0}, abstain=True)
        assert abstention_correct([_d("a", 0.1)], labels) is True

    def test_incorrect_when_something_relevant_is_presented(self):
        labels = QueryLabels(grades={"a": 2}, abstain=True)
        assert abstention_correct([_d("a", 0.1)], labels) is False

    def test_none_on_non_abstain_strata(self):
        labels = QueryLabels(grades={"a": 2}, abstain=False)
        assert abstention_correct([_d("a", 0.1)], labels) is None

    def test_leakage_names_the_mechanism(self):
        labels = QueryLabels(
            grades={"good": 3},
            distractor_classes={"d1": "decay_bait", "d2": "lexical_bait", "d3": "decay_bait"},
        )
        delivered = [_d("good", 0.9), _d("d1", 0.8), _d("d2", 0.7), _d("d3", 0.6)]
        assert leakage_by_class(delivered, labels, 4) == {"decay_bait": 2, "lexical_bait": 1}

    def test_no_leakage_reports_empty(self):
        labels = QueryLabels(grades={"good": 3}, distractor_classes={"d1": "decay_bait"})
        assert leakage_by_class([_d("good", 0.9)], labels, 4) == {}


class TestComposition:
    def test_counts_types_and_tiers(self):
        delivered = [
            _d("a", 0.9, memory_type="conversation", tier="hot"),
            _d("b", 0.8, memory_type="ingested", tier="cold"),
            _d("c", 0.7, memory_type="conversation", tier="cold"),
        ]
        result = composition(delivered)
        assert result["by_type"] == {"conversation": 2, "ingested": 1}
        assert result["by_tier"] == {"hot": 1, "cold": 2}


class TestRollUp:
    def test_model_visible_metrics_exclude_profile(self):
        """prompt_builder renders profile separately and caps only the
        remainder at 4, so the k=4 metrics must be over the non-profile slice."""
        labels = QueryLabels(grades={"p": 3, "a": 3, "b": 2, "c": 1, "d": 1, "e": 1})
        delivered = [
            _d("p", 0.99, memory_type="profile"),
            _d("a", 0.9),
            _d("b", 0.8),
            _d("c", 0.7),
            _d("d", 0.6),
            _d("e", 0.5),
        ]
        result = evaluate_query(delivered, labels)
        # Non-profile top 4 is a,b,c,d -- all graded, so no contamination.
        assert result["contamination_at_4"] == 0.0
        assert result["delivered_count"] == 6
        assert result["composition"]["by_type"]["profile"] == 1

    def test_compare_to_reference_shape(self):
        ref = [_d("a", 0.9), _d("b", 0.8)]
        arm = [_d("b", 0.9), _d("a", 0.8)]
        result = compare_to_reference(arm, ref)
        assert set(result) == {
            "selection_jaccard",
            "selection_jaccard_at_4",
            "rank_displacement",
        }
        assert result["selection_jaccard"] == 1.0


class TestMeanIgnoringNone:
    def test_skips_none_rather_than_scoring_it_zero(self):
        """An arm that correctly abstains must not be punished for it."""
        assert mean_ignoring_none([1.0, None, 0.5]) == pytest.approx(0.75)

    def test_all_none_is_none(self):
        assert mean_ignoring_none([None, None]) is None

    def test_empty_is_none(self):
        assert mean_ignoring_none([]) is None


class TestMeanPairwiseJaccard:
    """Within-arm query-independence. Hand-computed, not read off the impl.

    This metric was cited in issue #204 for seven arms while existing in no
    tool, which is why five of those rows were never reproducible. The values
    below are worked by hand so the implementation is pinned to arithmetic
    rather than to itself.
    """

    def test_identical_sets_score_one(self):
        sets = [["a", "b", "c"], ["a", "b", "c"], ["c", "b", "a"]]
        assert mean_pairwise_jaccard(sets) == 1.0

    def test_disjoint_sets_score_zero(self):
        assert mean_pairwise_jaccard([["a", "b"], ["c", "d"], ["e", "f"]]) == 0.0

    def test_order_is_ignored(self):
        assert mean_pairwise_jaccard([["a", "b"], ["b", "a"]]) == 1.0

    def test_hand_computed_three_way(self):
        # pairs: {a,b}v{b,c} = 1/3 ; {a,b}v{c,d} = 0 ; {b,c}v{c,d} = 1/3
        # mean = (1/3 + 0 + 1/3) / 3 = 2/9
        result = mean_pairwise_jaccard([["a", "b"], ["b", "c"], ["c", "d"]])
        assert result == pytest.approx(2 / 9)

    def test_partial_overlap_two_sets(self):
        # {a,b,c} v {b,c,d} -> intersection 2, union 4
        assert mean_pairwise_jaccard([["a", "b", "c"], ["b", "c", "d"]]) == pytest.approx(0.5)

    def test_duplicates_within_a_set_do_not_inflate(self):
        assert mean_pairwise_jaccard([["a", "a", "b"], ["a", "b"]]) == 1.0

    def test_two_empty_sets_agree_completely(self):
        # Both arms delivered nothing; that is agreement, not diversity.
        # Scoring 0.0 here would read as a maximally query-responsive arm.
        assert mean_pairwise_jaccard([[], []]) == 1.0

    def test_one_empty_against_one_populated_scores_zero(self):
        assert mean_pairwise_jaccard([[], ["a"]]) == 0.0

    def test_undefined_below_two_sets(self):
        assert mean_pairwise_jaccard([]) is None
        assert mean_pairwise_jaccard([["a", "b"]]) is None

    def test_distinct_from_selection_jaccard(self):
        # selection_jaccard compares two arms; this compares queries within
        # one arm. Conflating them is the error that made the #204 table
        # unreproducible from an artifact that reports the former.
        from tools.retrieval_ablation.metrics import selection_jaccard

        a = [_d("x", 1.0), _d("y", 0.9)]
        b = [_d("x", 1.0), _d("y", 0.9)]
        assert selection_jaccard(a, b) == 1.0
        assert mean_pairwise_jaccard([["x", "y"], ["p", "q"]]) == 0.0
