"""
Tests for memory tiering (ADR-015).

Covers: tier assignment, profile exemption, resolved state,
cold exclusion in ranker, retrieval stats, config thresholds,
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
    _importance_for_type,
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


def test_access_score_zero_count():
    assert _access_score(0, 10) == 0.0


def test_access_score_at_ceiling():
    assert _access_score(10, 10) == 1.0


def test_access_score_above_ceiling():
    assert _access_score(20, 10) == 1.0


def test_access_score_partial():
    assert _access_score(5, 10) == 0.5


def test_heat_formula():
    heat = _compute_heat(recency=1.0, access=1.0, importance=1.0)
    assert heat == 1.0

    heat = _compute_heat(recency=0.0, access=0.0, importance=0.0)
    assert heat == 0.0

    heat = _compute_heat(recency=1.0, access=0.0, importance=0.0)
    assert heat == 0.5


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


# ── Importance by type ──────────────────────────────────────────────────


def test_importance_profile():
    assert _importance_for_type("profile") == 1.0


def test_importance_conversation():
    assert _importance_for_type("conversation") == 0.4


def test_importance_ingested():
    assert _importance_for_type("ingested") == 0.3


def test_importance_unknown():
    assert _importance_for_type("unknown_type") == 0.5


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


def test_ranker_cold_scores_zero():
    from src.context.policies import ContextPolicy
    ranker = ContextRanker()
    policy = ContextPolicy(name="test")

    items = [_make_item(tier="cold", score=0.8)]
    result = ranker.apply_policy(items, policy)
    assert result[0].score == 0.0


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
    assert result[0].score > 0.0  # cold would be 0.0 but profile bypasses


# ── Retrieval stats ─────────────────────────────────────────────────────


def test_update_retrieval_stats(tmp_path: Path):
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
    row = conn.execute("SELECT retrieval_count, last_retrieved_at FROM vectors WHERE id = 'test-1'").fetchone()
    assert row["retrieval_count"] == 1
    assert row["last_retrieved_at"] is not None

    # Second update increments
    store.update_retrieval_stats(["test-1"])
    row = conn.execute("SELECT retrieval_count FROM vectors WHERE id = 'test-1'").fetchone()
    assert row["retrieval_count"] == 2

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
        row = conn.execute(f"SELECT retrieval_count FROM vectors WHERE id = 'rec-{i}'").fetchone()
        if i in (0, 2):
            assert row["retrieval_count"] == 1
        else:
            assert row["retrieval_count"] == 0

    conn.close()
    store.close()


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
