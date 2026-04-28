"""
tests/test_temporal_decay.py

Tests for the multiplicative temporal decay weighting in ContextRanker.
Verifies that memory types decay at the correct rates based on record age.
"""

from datetime import datetime, timedelta, timezone

import pytest

from src.context.models import ContextItem
from src.context.ranker import ContextRanker


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


class TestTemporalDecayWeight:
    """Unit tests for ContextRanker._temporal_decay_weight()."""

    def setup_method(self):
        self.ranker = ContextRanker()

    # ---- No-decay types ----

    def test_profile_no_decay_regardless_of_age(self):
        """Profile items are reference material — never decayed."""
        for days in [0, 30, 365, 1000]:
            item = _make_item("profile", days_old=days)
            assert self.ranker._temporal_decay_weight(item) == 1.0

    def test_reference_no_decay(self):
        """Reference items are reference material — never decayed."""
        item = _make_item("reference", days_old=200)
        assert self.ranker._temporal_decay_weight(item) == 1.0

    def test_ingested_no_decay(self):
        """Ingested items are reference material — never decayed."""
        item = _make_item("ingested", days_old=200)
        assert self.ranker._temporal_decay_weight(item) == 1.0

    # ---- Reflection decay tiers ----

    def test_reflection_fresh_no_decay(self):
        """Reflection within 7 days gets full weight."""
        item = _make_item("reflection", days_old=5)
        assert self.ranker._temporal_decay_weight(item) == 1.0

    def test_reflection_15_days(self):
        """Reflection at 15 days falls in the <=30d tier (0.80)."""
        item = _make_item("reflection", days_old=15)
        assert self.ranker._temporal_decay_weight(item) == 0.80

    def test_reflection_60_days(self):
        """Reflection at 60 days falls in the <=90d tier (0.60)."""
        item = _make_item("reflection", days_old=60)
        assert self.ranker._temporal_decay_weight(item) == 0.60

    def test_reflection_120_days(self):
        """Reflection at 120 days falls in the >90d tier (0.40)."""
        item = _make_item("reflection", days_old=120)
        assert self.ranker._temporal_decay_weight(item) == 0.40

    # ---- Ephemeral type decay (conversation, journal, session, decision) ----

    def test_conversation_fresh_no_decay(self):
        """Conversation within 3 days gets full weight."""
        item = _make_item("conversation", days_old=2)
        assert self.ranker._temporal_decay_weight(item) == 1.0

    def test_conversation_10_days(self):
        """Conversation at 10 days falls in the <=14d tier (0.45)."""
        item = _make_item("conversation", days_old=10)
        assert self.ranker._temporal_decay_weight(item) == 0.45

    def test_journal_5_days(self):
        """Journal at 5 days falls in the <=7d tier (0.70)."""
        item = _make_item("journal", days_old=5)
        assert self.ranker._temporal_decay_weight(item) == 0.70

    def test_session_20_days(self):
        """Session at 20 days falls in the <=30d tier (0.25)."""
        item = _make_item("session", days_old=20)
        assert self.ranker._temporal_decay_weight(item) == 0.25

    def test_decision_45_days(self):
        """Decision at 45 days falls in the >30d tier (0.10)."""
        item = _make_item("decision", days_old=45)
        assert self.ranker._temporal_decay_weight(item) == 0.10

    # ---- Default decay (other types) ----

    def test_state_15_days(self):
        """State type at 15 days falls in the <=30d default tier (0.50)."""
        item = _make_item("state", days_old=15)
        assert self.ranker._temporal_decay_weight(item) == 0.50

    def test_task_100_days(self):
        """Task type at 100 days falls in the >90d default tier (0.15)."""
        item = _make_item("task", days_old=100)
        assert self.ranker._temporal_decay_weight(item) == 0.15

    # ---- Fresh item ----

    def test_fresh_item_today_gets_1(self):
        """An item from today always gets weight 1.0 regardless of type."""
        for mem_type in ["conversation", "reflection", "state", "profile"]:
            item = _make_item(mem_type, days_old=0)
            assert self.ranker._temporal_decay_weight(item) == 1.0

    # ---- Unparseable timestamp ----

    def test_unparseable_timestamp_returns_1(self):
        """Items with bad timestamps get no penalty (weight 1.0)."""
        item = _make_item("conversation", timestamp="not-a-date")
        assert self.ranker._temporal_decay_weight(item) == 1.0

    def test_none_timestamp_returns_1(self):
        """Items with None timestamp get no penalty (weight 1.0)."""
        item = _make_item("conversation", timestamp=None, days_old=None)
        item.timestamp = None
        assert self.ranker._temporal_decay_weight(item) == 1.0

    # ---- Hyphenated vault timestamp format ----

    def test_hyphenated_timestamp_parsed(self):
        """Vault-format timestamps (YYYY-MM-DDTHH-MM-SS) are parsed correctly."""
        dt = datetime.now(timezone.utc) - timedelta(days=10)
        hyph_ts = dt.strftime("%Y-%m-%dT%H-%M-%S")
        item = _make_item("conversation", timestamp=hyph_ts)
        # 10 days old conversation => <=14d tier => 0.45
        assert self.ranker._temporal_decay_weight(item) == 0.45


class TestRecencyBoostHyphenatedTimestamp:
    """Regression: _recency_boost previously did not parse the hyphenated
    state-layer timestamp format (YYYY-MM-DDTHH-MM-SS) and silently
    returned 0.0 for any record stamped that way. Fresh state records
    therefore lost their +0.18 recency boost and could be outranked by
    older records carrying ISO timestamps. Fix delegates to
    _parse_age_days which handles all three formats uniformly."""

    def setup_method(self):
        self.ranker = ContextRanker()

    @pytest.mark.parametrize(
        "days_old,expected_boost",
        [
            (1, 0.18),     # ≤7 days
            (7, 0.18),
            (15, 0.12),    # ≤30 days
            (60, 0.06),    # ≤90 days
            (200, 0.02),   # ≤365 days
            (500, -0.03),  # >365 days
        ],
    )
    def test_recency_boost_handles_hyphenated_state_timestamp(
        self, days_old, expected_boost
    ):
        dt = datetime.now(timezone.utc) - timedelta(days=days_old)
        hyph_ts = dt.strftime("%Y-%m-%dT%H-%M-%S")
        assert self.ranker._recency_boost(hyph_ts) == expected_boost

    def test_recency_boost_iso_format_still_works(self):
        """ISO 8601 path must still work — the fix added hyphenated support
        without breaking the existing format."""
        dt = datetime.now(timezone.utc) - timedelta(days=3)
        iso_ts = dt.isoformat()
        assert self.ranker._recency_boost(iso_ts) == 0.18

    def test_recency_boost_unix_epoch_still_works(self):
        """Unix epoch path must still work."""
        dt = datetime.now(timezone.utc) - timedelta(days=3)
        epoch_ts = str(dt.timestamp())
        assert self.ranker._recency_boost(epoch_ts) == 0.18

    def test_recency_boost_unparseable_returns_zero(self):
        assert self.ranker._recency_boost("not-a-timestamp") == 0.0
        assert self.ranker._recency_boost(None) == 0.0
        assert self.ranker._recency_boost("") == 0.0

    # ---- Integration: rank() applies decay ----

    def test_rank_applies_decay_to_memory_items(self):
        """rank() should multiply scores by temporal decay weight."""
        old_item = _make_item("conversation", days_old=60, score=0.9)
        fresh_item = _make_item("conversation", days_old=1, score=0.7)

        ranked_mem, _ = self.ranker.rank([old_item, fresh_item], [])

        # Fresh item (1 day) should rank above old item (60 days, heavy decay)
        assert ranked_mem[0].id == fresh_item.id
        assert ranked_mem[1].id == old_item.id

    def test_rank_applies_decay_to_reflections(self):
        """rank() should multiply reflection scores by temporal decay weight."""
        old_ref = _make_item("reflection", days_old=100, score=0.9)
        fresh_ref = _make_item("reflection", days_old=3, score=0.7)

        _, ranked_ref = self.ranker.rank([], [old_ref, fresh_ref])

        # Fresh reflection should rank above old one after decay
        assert ranked_ref[0].id == fresh_ref.id
        assert ranked_ref[1].id == old_ref.id
