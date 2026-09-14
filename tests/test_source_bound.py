"""
tests/test_source_bound.py

Unit tests for src/tiering/source_bound.py -- ADR-015 amendment (PR #180),
implementation step 2. Pure logic, no vault: bound_tier() takes a
pre-resolved tier_index dict rather than touching storage.
"""

from __future__ import annotations

from src.tiering.source_bound import TIER_RANK, bound_tier


class TestTierRank:
    def test_ordering_is_cold_warm_hot(self):
        assert TIER_RANK["cold"] < TIER_RANK["warm"] < TIER_RANK["hot"]


class TestBoundTierCapsDownwardOnly:
    def test_hotter_natural_tier_is_capped_to_source_max(self):
        assert bound_tier("hot", ["a"], {"a": "warm"}) == "warm"

    def test_colder_natural_tier_is_left_alone(self):
        """A record may be colder than its sources; it may not be hotter."""
        assert bound_tier("cold", ["a"], {"a": "hot"}) == "cold"

    def test_equal_tier_is_left_alone(self):
        assert bound_tier("warm", ["a"], {"a": "warm"}) == "warm"

    def test_bound_uses_the_hottest_of_multiple_sources(self):
        assert bound_tier("hot", ["a", "b", "c"], {
            "a": "cold", "b": "hot", "c": "warm",
        }) == "hot"


class TestLegacyFloor:
    def test_empty_source_list_floors_at_cold(self):
        assert bound_tier("hot", [], {"a": "hot"}) == "cold"

    def test_none_source_list_floors_at_cold(self):
        assert bound_tier("hot", None, {"a": "hot"}) == "cold"

    def test_nonempty_list_with_nothing_resolvable_floors_at_cold(self):
        """Every id present, none of them known to tier_index -- effectively
        unprovenanced, same floor as having no source_record_ids at all."""
        assert bound_tier("hot", ["deleted-1", "deleted-2"], {}) == "cold"


class TestPartialResolution:
    def test_bounds_against_the_resolvable_subset(self):
        """One real source plus one that no longer resolves: bounds against
        what IS found, does not floor at cold for the whole record."""
        assert bound_tier("hot", ["real", "deleted"], {"real": "warm"}) == "warm"

    def test_all_but_one_source_missing_still_uses_the_one_found(self):
        assert bound_tier("hot", ["a", "b", "c"], {"b": "cold"}) == "cold"


class TestNonVacuousness:
    def test_all_hot_sources_do_not_artificially_lower_the_bound(self):
        """The bound is a ceiling, never a floor above what the caller's
        natural_tier already was -- confirms this cannot silently become
        a no-op check that always returns the source value."""
        assert bound_tier("warm", ["a"], {"a": "hot"}) == "warm"
