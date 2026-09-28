"""
tests/test_temporal_decay.py

Age handling: one model, owned by ADR-015.

This file used to test `ContextRanker._temporal_decay_weight` and
`_recency_boost` -- a stepwise per-type multiplier and an additive freshness
bonus, applied at three separate sites. ADR-044 decision 3 absorbed the first
into `TieringService` as a per-type exponential halflife, and consolidated the
second into the bounded prior, so the ranker now applies exactly one age
multiplier: tier.

What that means for these tests, stated because it decides which survived:

  RETIRED. The ladder-step assertions (reflection at 15 days is exactly 0.80,
  ephemeral at 10 days is exactly 0.45, and so on). They pinned the
  coordinates of a stepwise curve that no longer exists. A halflife is not a
  retuning of a ladder, so there is nothing to carry the numbers over to.

  MOVED. The no-decay exemption for profile, reference and ingested. Same
  claim, new owner: halflife_for_type returns None for exactly those three.

  MOVED. The three-format timestamp parsing, which is the regression this
  file's second class was written for and is still worth pinning. It now
  reads back as a bucket NAME rather than a bonus, because that is what
  _recency_bucket returns; the parsing underneath is the same
  _parse_age_days.

  REPLACED. "rank() lets age invert a score gap." Age no longer reaches
  rank() at all, and the prior's total swing is 1.21:1, so a 0.9-vs-0.7 gap
  is now beyond what any metadata can overturn there. The property moved to
  tier, so it is asserted against tier.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.context import prior
from src.context.models import ContextItem
from src.context.policies import ContextPolicy
from src.context.ranker import COLD_MULTIPLIER, ContextRanker
from src.tiering.tiering_service import (
    _DEFAULT_FAMILY_HALFLIFE_DAYS,
    _EPHEMERAL_HALFLIFE_DAYS,
    _recency_score,
    halflife_for_type,
)


def _make_item(
    memory_type: str,
    days_old: int | None = 0,
    timestamp: str | None = None,
    score: float = 0.8,
) -> ContextItem:
    """Create a ContextItem with a timestamp N days in the past."""
    if timestamp is None and days_old is not None:
        dt = datetime.now(timezone.utc) - timedelta(days=days_old)
        timestamp = dt.isoformat()

    return ContextItem(
        id=f"test-{memory_type}-{days_old}",
        content="Test content for temporal decay verification.",
        source="test",
        item_type=memory_type,
        memory_type=memory_type,
        score=score,
        timestamp=timestamp,
    )


def _days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


class TestReferenceClassesDoNotDecay:
    """The exemption, preserved across the move from ranker to TieringService."""

    @pytest.mark.parametrize("memory_type", ["profile", "reference", "ingested"])
    def test_reference_class_has_no_halflife(self, memory_type):
        assert halflife_for_type(memory_type) is None

    @pytest.mark.parametrize("memory_type", ["profile", "reference", "ingested"])
    def test_the_exemption_is_age_independent(self, memory_type):
        """None is returned for the type, so no age can produce a decay."""
        assert halflife_for_type(memory_type) is None


class TestPerTypeHalflife:
    """The three fitted families, and that they are ordered as intended.

    Values rather than curve coordinates: a halflife is one number per type,
    so pinning it is pinning the whole curve. That is the difference from the
    ladder these replaced, where a value had to be pinned per bucket and the
    ladder's shape was implicit in the set of them.
    """

    @pytest.mark.parametrize(
        "memory_type", ["conversation", "journal", "session", "decision"]
    )
    def test_ephemeral_types_take_the_short_halflife(self, memory_type):
        assert halflife_for_type(memory_type) == _EPHEMERAL_HALFLIFE_DAYS

    def test_reflection_takes_the_long_halflife(self):
        assert halflife_for_type("reflection") == 122.0

    @pytest.mark.parametrize("memory_type", ["state", "task", "summary", "unknown_type"])
    def test_everything_else_takes_the_default_family(self, memory_type):
        """The default family is the catch-all, as _DEFAULT_DECAY was.

        Pinned because an earlier version fell through to the configured
        global (30) instead of the fitted 52, so the comment stating the fit
        and the code disagreed and the absorbed ladder was silently replaced
        by a curve nobody derived.
        """
        assert halflife_for_type(memory_type) == _DEFAULT_FAMILY_HALFLIFE_DAYS

    def test_the_families_are_ordered_ephemeral_default_reflection(self):
        """A conversation turn and a reflection of the same age must not decay
        identically -- the reason ADR-044 absorbed the curve rather than
        deleting it."""
        assert (
            halflife_for_type("conversation")
            < halflife_for_type("task")
            < halflife_for_type("reflection")
        )


class TestTheCurveIsExponential:
    """Functional form, asserted rather than assumed.

    ADR-044 records that the form had to be settled before the values, because
    two curves of different form cannot be compared by tuning. The halflife
    property is what makes it checkable: at exactly one halflife the score is
    one half, at two it is one quarter.
    """

    def test_score_is_one_at_zero_age(self):
        assert _recency_score(_days_ago(0), None, halflife_days=15) == pytest.approx(1.0)

    def test_score_halves_at_one_halflife(self):
        assert _recency_score(_days_ago(15), None, halflife_days=15) == pytest.approx(
            0.5, abs=1e-9
        )

    def test_score_quarters_at_two_halflives(self):
        assert _recency_score(_days_ago(30), None, halflife_days=15) == pytest.approx(
            0.25, abs=1e-9
        )

    def test_the_ephemeral_fit_reproduces_its_ladder_knee(self):
        """15 days was fitted at the ephemeral ladder's x0.25-at-30-days knee.

        This is the one place the retired ladder's numbers still matter: the
        halflife is only defensible if it reproduces the point the ladder was
        load-bearing at. ranker.decay.bucket.ephemeral=older fired 256 of 256
        times in the production window, so 30 days and beyond is where that
        curve actually did its work.
        """
        assert _recency_score(
            _days_ago(30), None, halflife_days=int(_EPHEMERAL_HALFLIFE_DAYS)
        ) == pytest.approx(0.25, abs=1e-9)

    def test_an_unparseable_reference_does_not_decay_to_a_number(self):
        assert _recency_score("not-a-timestamp", None, halflife_days=15) == 0.0
        assert _recency_score(None, None, halflife_days=15) == 0.0


class TestRecencyBucketParsesEveryTimestampFormat:
    """Regression guard, carried over from _recency_boost.

    The original defect: the recency lookup did not parse the hyphenated
    state-layer format (YYYY-MM-DDTHH-MM-SS) and silently returned the neutral
    value for any record stamped that way, so fresh state records lost their
    boost and could be outranked by older records carrying ISO timestamps.

    The fix was to delegate to _parse_age_days, which handles all three
    formats, and that delegation is what these tests protect. What changed
    under ADR-044 is only the return type: a bucket name instead of a bonus.
    The bucket boundaries (7 / 30 / 90 / 365) are unchanged.
    """

    def setup_method(self):
        self.ranker = ContextRanker()

    @pytest.mark.parametrize(
        "days_old,expected_bucket",
        [
            (1, "d7"),
            (7, "d7"),
            (15, "d30"),
            (60, "d90"),
            (200, "d365"),
            (500, "older"),
        ],
    )
    def test_hyphenated_state_timestamp_is_parsed(self, days_old, expected_bucket):
        dt = datetime.now(timezone.utc) - timedelta(days=days_old)
        hyph_ts = dt.strftime("%Y-%m-%dT%H-%M-%S")
        assert self.ranker._recency_bucket(hyph_ts) == expected_bucket

    def test_iso_format_still_works(self):
        dt = datetime.now(timezone.utc) - timedelta(days=3)
        assert self.ranker._recency_bucket(dt.isoformat()) == "d7"

    def test_unix_epoch_still_works(self):
        dt = datetime.now(timezone.utc) - timedelta(days=3)
        assert self.ranker._recency_bucket(str(dt.timestamp())) == "d7"

    def test_unparseable_takes_the_neutral_bucket(self):
        """The neutral element moved from additive 0.0 to multiplicative 1.0.

        Both mean "this record gets no recency adjustment". Asserted through
        the prior rather than on the bucket name alone, because the name is
        only neutral if the table maps it to 1.0 -- which is the property that
        actually matters and the one a future edit could break.
        """
        for bad in ("not-a-timestamp", None, ""):
            bucket = self.ranker._recency_bucket(bad)
            assert bucket == "unparsed"
            assert prior.RECENCY[bucket] == 1.0

    def test_the_buckets_are_ordered_fresh_to_stale(self):
        """The ladder keeps its previous relative ordering as multipliers."""
        order = ["d7", "d30", "d90", "d365", "older"]
        values = [prior.RECENCY[b] for b in order]
        assert values == sorted(values, reverse=True)
        assert prior.RECENCY["older"] < 1.0 < prior.RECENCY["d7"]


class TestAgeReachesRankingThroughTierOnly:
    """The property that moved, asserted where it now lives.

    Two tests used to assert that rank() let age invert a score gap: a 1-day
    record at 0.7 finishing above a 60-day record at 0.9. That is now
    impossible in rank() by construction -- the prior's whole swing is
    1.1278 / 0.9339 = 1.21, and the gap is 1.29 -- and it is impossible on
    purpose, because ADR-044 decision 3 moved age out of rank().

    So the claim is tested against tier, which is where age arrives now.
    """

    def setup_method(self):
        self.ranker = ContextRanker()
        self.policy = ContextPolicy(name="test", memory_weight=1.0)

    def test_rank_no_longer_lets_age_invert_a_score_gap(self):
        """The retired behaviour, pinned as retired.

        Not a regression: an inversion this large from query-independent
        metadata is exactly what the bound exists to prevent. Pinned so that
        re-introducing an unbounded age term in rank() fails here rather than
        being discovered by a measurement six months later.
        """
        old_item = _make_item("conversation", days_old=60, score=0.9)
        fresh_item = _make_item("conversation", days_old=1, score=0.7)

        ranked, _ = self.ranker.rank([old_item, fresh_item], [])

        assert ranked[0].id == old_item.id, (
            "rank() inverted a 0.2 score gap on age alone; the prior's bound "
            "should make that impossible"
        )

    def test_the_priors_total_swing_cannot_close_the_gap(self):
        """The arithmetic behind the test above, stated so the bound is the
        reason rather than a coincidence of these two fixtures."""
        widest = prior.PRIOR_MAX / prior.PRIOR_MIN
        assert widest < (0.9 / 0.7)

    def test_tier_is_where_age_can_still_reorder(self):
        """A cold record loses to a hot one at equal similarity, and tier is
        the only stage that did it."""
        hot = _make_item("conversation", days_old=1, score=0.5)
        hot.tier = "hot"
        cold = _make_item("conversation", days_old=400, score=0.5)
        cold.tier = "cold"
        cold.id = "cold-record"

        adjusted = self.ranker.apply_policy([cold, hot], self.policy)
        by_id = {i.id: i.score for i in adjusted}

        assert by_id["cold-record"] == pytest.approx(0.5 * COLD_MULTIPLIER)
        assert by_id["cold-record"] < by_id[hot.id]

    def test_reflection_scores_are_not_decayed_in_rank_either(self):
        """The reflection half of the same retired behaviour."""
        old_ref = _make_item("reflection", days_old=100, score=0.9)
        fresh_ref = _make_item("reflection", days_old=3, score=0.7)

        _, ranked = self.ranker.rank([], [old_ref, fresh_ref])

        assert ranked[0].id == old_ref.id
