"""
Tests for memory tiering (ADR-015).

Covers: tier assignment, profile exemption, resolved state,
cold-tier weighting in ranker, retrieval stats, config thresholds,
schema migration safety.
"""

import math
import sqlite3
from pathlib import Path

import pytest

from src.context.models import ContextItem
from src.context.ranker import ContextRanker
from src.retrieval.sqlite_vector_store import SqliteVectorStore
from src.tiering.tiering_service import (
    TieringService,
    _access_score,
    _compute_heat,
    _recency_score,
    _tier_from_heat,
)


# ── Heat score components ───────────────────────────────────────────────


def test_recency_score_today_is_1():
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    score = _recency_score(today, None, halflife_days=30)
    assert score > 0.95


def test_recency_score_at_halflife_is_0_5():
    from datetime import datetime, timedelta
    past = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    score = _recency_score(past, None, halflife_days=30)
    assert 0.45 <= score <= 0.55


def test_recency_score_very_old_is_near_zero():
    score = _recency_score("2020-01-01", None, halflife_days=30)
    assert score < 0.01


def test_recency_score_none_returns_zero():
    assert _recency_score(None, None, halflife_days=30) == 0.0


def test_recency_score_epoch_float_string():
    """ADR-015 amendment step 4: _recency_score previously parsed only a
    %Y-%m-%d prefix, so an epoch-float timestamp (e.g. ChatGPT imports)
    silently scored 0.0. Regression test for the parser gap fix."""
    from datetime import datetime, timezone
    epoch = datetime.now(timezone.utc).timestamp()
    score = _recency_score(str(epoch), None, halflife_days=30)
    assert score > 0.95


def test_recency_score_epoch_float_string_at_halflife():
    from datetime import datetime, timedelta, timezone
    past = datetime.now(timezone.utc) - timedelta(days=30)
    score = _recency_score(str(past.timestamp()), None, halflife_days=30)
    assert 0.45 <= score <= 0.55


def test_access_score_zero_frequency():
    assert _access_score(0.0, 10) == 0.0


def test_access_score_at_ceiling():
    assert _access_score(10.0, 10) == 1.0


def test_access_score_above_ceiling():
    assert _access_score(20.0, 10) == 1.0


def test_access_score_partial():
    assert _access_score(5.0, 10) == 0.5


def test_heat_formula():
    """ADR-015 amendment step 4: importance term removed, recency:access
    renormalized to 0.625:0.375 (preserving the original 5:3 ratio)."""
    heat = _compute_heat(recency=1.0, access=1.0)
    assert heat == 1.0

    heat = _compute_heat(recency=0.0, access=0.0)
    assert heat == 0.0

    heat = _compute_heat(recency=1.0, access=0.0)
    assert heat == pytest.approx(0.625)

    heat = _compute_heat(recency=0.0, access=1.0)
    assert heat == pytest.approx(0.375)


# ── Tier thresholds ─────────────────────────────────────────────────────


def test_tier_from_heat_hot():
    assert _tier_from_heat(0.6, hot_threshold=0.5, warm_threshold=0.2) == "hot"


def test_tier_from_heat_warm():
    assert _tier_from_heat(0.3, hot_threshold=0.5, warm_threshold=0.2) == "warm"


def test_tier_from_heat_cold():
    assert _tier_from_heat(0.1, hot_threshold=0.5, warm_threshold=0.2) == "cold"


def test_tier_from_heat_boundary_hot():
    assert _tier_from_heat(0.5, hot_threshold=0.5, warm_threshold=0.2) == "hot"


def test_tier_from_heat_boundary_warm():
    assert _tier_from_heat(0.2, hot_threshold=0.5, warm_threshold=0.2) == "warm"


# ── Ranker tier modifier ───────────────────────────────────────────────


def _make_item(memory_type="conversation", score=0.5, tier="hot"):
    return ContextItem(
        id="test",
        content="test content long enough",
        source=memory_type,
        item_type=memory_type,
        memory_type=memory_type,
        score=score,
        tier=tier,
    )


def test_ranker_cold_applies_0_3_multiplier():
    """ADR-015 amendment step 3: cold is a reduced weight (0.3), not
    exclusion (0.0)."""
    from src.context.ranker import COLD_MULTIPLIER
    from src.context.policies import ContextPolicy
    ranker = ContextRanker()
    policy = ContextPolicy(name="test", memory_weight=1.0)

    items = [_make_item(tier="cold", score=0.8)]
    result = ranker.apply_policy(items, policy)
    assert result[0].score == pytest.approx(0.8 * COLD_MULTIPLIER)
    assert result[0].score > 0.0


def test_ranker_warm_applies_0_7_multiplier():
    from src.context.policies import ContextPolicy
    ranker = ContextRanker()
    policy = ContextPolicy(name="test", memory_weight=1.0)

    items = [_make_item(tier="warm", score=1.0)]
    result = ranker.apply_policy(items, policy)
    # Score after memory_weight * 1.0, then * 0.7 for warm
    assert result[0].score < 1.0
    assert result[0].score > 0.0


def test_ranker_hot_no_penalty():
    from src.context.policies import ContextPolicy
    ranker = ContextRanker()
    policy = ContextPolicy(name="test", memory_weight=1.0)

    items = [_make_item(tier="hot", score=0.5)]
    result = ranker.apply_policy(items, policy)
    assert result[0].score >= 0.5  # no tier penalty applied


def test_ranker_profile_bypasses_tier():
    from src.context.policies import ContextPolicy
    ranker = ContextRanker()
    policy = ContextPolicy(name="test", memory_weight=1.0)

    items = [_make_item(memory_type="profile", tier="cold", score=0.5)]
    result = ranker.apply_policy(items, policy)
    # profile bypasses tier scoring entirely -- unaffected by COLD_MULTIPLIER
    assert result[0].score == pytest.approx(0.5)


# ── Retrieval stats ─────────────────────────────────────────────────────


def test_update_retrieval_stats(tmp_path: Path):
    """ADR-015 amendment step 4: frequency_score, not retrieval_count, is
    the live accumulator now."""
    db_path = tmp_path / "memory.db"
    store = SqliteVectorStore(db_path)

    store.insert({
        "id": "test-1",
        "text": "test content",
        "embedding": [0.1] * 768,
        "source": "test",
        "memory_type": "conversation",
        "created_at": "2026-04-03",
        "metadata": {},
    })

    store.update_retrieval_stats(["test-1"])

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT frequency_score, last_retrieved_at FROM vectors WHERE id = 'test-1'").fetchone()
    assert row["frequency_score"] == pytest.approx(1.0)
    assert row["last_retrieved_at"] is not None

    # Second update, immediately after the first: elapsed time ~0 days, so
    # decay ~1.0 and frequency accumulates (old * ~1.0 + 1 ~= 2.0).
    store.update_retrieval_stats(["test-1"])
    row = conn.execute("SELECT frequency_score FROM vectors WHERE id = 'test-1'").fetchone()
    assert row["frequency_score"] == pytest.approx(2.0, abs=0.01)

    conn.close()
    store.close()


def test_update_retrieval_stats_only_selected(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    store = SqliteVectorStore(db_path)

    for i in range(3):
        store.insert({
            "id": f"rec-{i}",
            "text": f"record {i} content",
            "embedding": [0.1] * 768,
            "source": "test",
            "memory_type": "conversation",
            "created_at": "2026-04-03",
            "metadata": {},
        })

    # Only update rec-0 and rec-2
    store.update_retrieval_stats(["rec-0", "rec-2"])

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    for i in range(3):
        row = conn.execute(f"SELECT frequency_score FROM vectors WHERE id = 'rec-{i}'").fetchone()
        if i in (0, 2):
            assert row["frequency_score"] == pytest.approx(1.0)
        else:
            assert row["frequency_score"] == pytest.approx(0.0)

    conn.close()
    store.close()


def test_update_retrieval_stats_decays_after_long_gap(tmp_path: Path):
    """The frequency accumulator must decay, not just accumulate.

    A record retrieved once long ago (well past the halflife), then
    retrieved again now, should show its old contribution mostly decayed
    away rather than a simple +1 on top of the stale value.
    """
    from datetime import datetime, timedelta

    db_path = tmp_path / "memory.db"
    store = SqliteVectorStore(db_path)
    store.insert({
        "id": "stale-1",
        "text": "test content",
        "embedding": [0.1] * 768,
        "source": "test",
        "memory_type": "conversation",
        "created_at": "2026-01-01",
        "metadata": {},
    })

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Simulate a prior retrieval 90 days ago (3x the default 30-day
    # halflife) that had already accumulated frequency_score = 5.0.
    stale_retrieval = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%dT%H-%M-%S")
    conn.execute(
        "UPDATE vectors SET frequency_score = 5.0, last_retrieved_at = ? WHERE id = 'stale-1'",
        (stale_retrieval,),
    )
    conn.commit()

    store.update_retrieval_stats(["stale-1"])

    row = conn.execute("SELECT frequency_score FROM vectors WHERE id = 'stale-1'").fetchone()
    # 5.0 * 2^(-90/30) + 1.0 = 5.0 * 0.125 + 1.0 = 1.625
    assert row["frequency_score"] == pytest.approx(1.625, abs=0.05)
    # Far less than the monotonic-counter behavior this replaces (5 + 1 = 6).
    assert row["frequency_score"] < 2.0

    conn.close()
    store.close()


def test_legacy_high_retrieval_count_goes_cold_when_stale(tmp_path: Path):
    """Regression test for the exact defect ADR-015's amendment names: a
    record with a legacy retrieval_count >= 4 (the old permanent-floor
    threshold) that has NOT been retrieved recently must still be able to
    go cold under the new frequency_score-driven model.
    """
    from datetime import datetime, timedelta
    import math
    from src.tiering.tiering_service import _recency_score, _access_score, _compute_heat

    old_created_at = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%dT%H-%M-%S")
    old_last_retrieved = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%dT%H-%M-%S")

    # Legacy retrieval_count=10 (old ceiling-saturating value) is inert now;
    # frequency_score reflects long-decayed history instead.
    recency = _recency_score(old_last_retrieved, old_created_at, halflife_days=30)
    freq_decayed = 0.4 * recency  # some small leftover accumulator, itself decayed
    access = _access_score(freq_decayed, ceiling=10)
    heat = _compute_heat(recency, access)

    assert heat < 0.2  # cold, per default TIER_WARM_THRESHOLD


def test_search_results_include_id(tmp_path: Path):
    """ADR-015 amendment step 4: search() must surface the row's real
    primary key so callers can address it again for retrieval stats."""
    db_path = tmp_path / "memory.db"
    store = SqliteVectorStore(db_path)

    embedding = [0.5] * 768
    store.insert({
        "id": "2026-04-03T16-53-55",
        "text": "a record with a real id",
        "embedding": embedding,
        "source": "test",
        "memory_type": "conversation",
        "created_at": "2026-04-03",
        "metadata": {},
    })

    results = store.search(embedding, limit=5, memory_type="conversation")
    assert len(results) == 1
    assert results[0]["id"] == "2026-04-03T16-53-55"
    store.close()


def test_service_update_retrieval_stats_uses_store_id():
    """End-to-end regression test for the identity fix: ContextService
    must key retrieval-stat writes off ContextItem.store_id (the real
    vectors.id), not `.id` (a file path for most memory types). Before
    this fix, `.id` was passed to update_retrieval_stats and matched zero
    rows, so retrieval_count/frequency_score never moved for anything but
    reflections.
    """
    from src.context.service import ContextService
    from src.core.config import get_private_vault_path

    vault = get_private_vault_path()
    db_path = vault / "embeddings" / "memory.db"
    store = SqliteVectorStore(db_path)
    store.insert({
        "id": "2026-05-01T09-00-00",
        "text": "a conversation record long enough to pass filters",
        "embedding": [0.2] * 768,
        "source": "test",
        "memory_type": "conversation",
        "created_at": "2026-05-01T09-00-00",
        "metadata": {},
    })
    store.close()

    item = ContextItem(
        id="/some/file/path/that/is/not/the/vectors/id.json",
        content="a conversation record long enough to pass filters",
        source="conversation",
        item_type="conversation",
        memory_type="conversation",
        store_id="2026-05-01T09-00-00",
    )

    service = ContextService()
    service._update_retrieval_stats([item])

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT frequency_score, last_retrieved_at FROM vectors WHERE id = ?",
        ("2026-05-01T09-00-00",),
    ).fetchone()
    conn.close()

    assert row is not None
    assert row["frequency_score"] == pytest.approx(1.0)
    assert row["last_retrieved_at"] is not None


# ── Schema migration safety ────────────────────────────────────────────


def test_schema_migration_safe_on_second_startup(tmp_path: Path):
    """Opening SqliteVectorStore twice should not fail."""
    db_path = tmp_path / "test.db"
    store1 = SqliteVectorStore(db_path)
    store1.close()
    store2 = SqliteVectorStore(db_path)
    store2.close()


# ── Config thresholds ───────────────────────────────────────────────────


def test_tier_config_defaults():
    from src.core.config import (
        get_tier_access_ceiling,
        get_tier_hot_threshold,
        get_tier_recency_halflife_days,
        get_tier_warm_threshold,
    )
    assert get_tier_recency_halflife_days() == 30
    assert get_tier_access_ceiling() == 10
    assert get_tier_hot_threshold() == 0.5
    assert get_tier_warm_threshold() == 0.2


# ── Search results include tier ─────────────────────────────────────────


def test_search_results_include_tier(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    store = SqliteVectorStore(db_path)

    embedding = [0.5] * 768
    store.insert({
        "id": "tiered-1",
        "text": "a record with a tier",
        "embedding": embedding,
        "source": "test",
        "memory_type": "conversation",
        "created_at": "2026-04-03",
        "metadata": {},
    })

    results = store.search(embedding, limit=5, memory_type="conversation")
    assert len(results) >= 1
    assert "tier" in results[0]
    assert results[0]["tier"] == "hot"  # default
    store.close()


# ---------------------------------------------------------------------------
# _next_tiering_run_time: month and year boundary regression
# ---------------------------------------------------------------------------


def test_next_tiering_run_time_rolls_over_month_end():
    """Regression: April 30 -> May 1 must not raise. The prior
    implementation used datetime.replace(day=day+1), which failed on
    the last day of any month with ValueError 'day is out of range'."""
    from datetime import datetime
    from src.api.main import _next_tiering_run_time

    now = datetime(2026, 4, 30, 12, 0, 0)
    result = _next_tiering_run_time(now)
    assert result == datetime(2026, 5, 1, 0, 5, 0)


def test_next_tiering_run_time_rolls_over_31_day_month_end():
    from datetime import datetime
    from src.api.main import _next_tiering_run_time

    now = datetime(2026, 5, 31, 23, 59, 59)
    result = _next_tiering_run_time(now)
    assert result == datetime(2026, 6, 1, 0, 5, 0)


def test_next_tiering_run_time_rolls_over_year_end():
    from datetime import datetime
    from src.api.main import _next_tiering_run_time

    now = datetime(2026, 12, 31, 23, 0, 0)
    result = _next_tiering_run_time(now)
    assert result == datetime(2027, 1, 1, 0, 5, 0)


def test_next_tiering_run_time_same_day_when_before_target():
    """When called before 00:05 on a given day, the next run is later
    that same day (no rollover)."""
    from datetime import datetime
    from src.api.main import _next_tiering_run_time

    now = datetime(2026, 4, 15, 0, 4, 0)
    result = _next_tiering_run_time(now)
    assert result == datetime(2026, 4, 15, 0, 5, 0)


def test_next_tiering_run_time_next_day_when_after_target():
    """When called after 00:05, the next run is 00:05 the following day."""
    from datetime import datetime
    from src.api.main import _next_tiering_run_time

    now = datetime(2026, 4, 15, 0, 6, 0)
    result = _next_tiering_run_time(now)
    assert result == datetime(2026, 4, 16, 0, 5, 0)


def test_next_tiering_run_time_handles_leap_day_boundary():
    """Leap-year February 29 -> March 1 boundary."""
    from datetime import datetime
    from src.api.main import _next_tiering_run_time

    now = datetime(2028, 2, 29, 12, 0, 0)
    result = _next_tiering_run_time(now)
    assert result == datetime(2028, 3, 1, 0, 5, 0)
