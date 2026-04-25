"""
tests/test_pattern_detector.py

Coverage for src/safety/pattern_detector.py — PatternSignal dataclass
and detect_t2_pattern over seeded retrieval items.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from src.context.models import ContextItem
from src.safety.pattern_detector import (
    PatternSignal,
    T2_CATEGORIES,
    T2_MIN_INSTANCES,
    T2_MIN_SESSIONS,
    T2_MIN_SIMILARITY,
    T2_RECENCY_DAYS,
    detect_t2_pattern,
)


# ---------------------------------------------------------------------------
# PatternSignal dataclass
# ---------------------------------------------------------------------------


def test_pattern_signal_dataclass_defaults() -> None:
    sig = PatternSignal(
        instance_count=3,
        session_count=2,
        has_named_party=False,
        max_similarity=0.85,
    )
    assert sig.category == "relational"
    assert sig.category in T2_CATEGORIES


def test_pattern_signal_is_frozen() -> None:
    sig = PatternSignal(
        instance_count=3,
        session_count=2,
        has_named_party=False,
        max_similarity=0.85,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        sig.instance_count = 4  # type: ignore[misc]


def test_pattern_signal_equality() -> None:
    a = PatternSignal(3, 2, False, 0.85)
    b = PatternSignal(3, 2, False, 0.85)
    assert a == b


# ---------------------------------------------------------------------------
# Helpers for seeding ContextItems
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _old_iso(days: int = 60) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _make_item(
    *,
    embedding: list[float] | None = None,
    session_id: str = "sess_a",
    timestamp: str | None = None,
    contains_named_third_party: bool = False,
    memory_type: str = "conversation",
    content: str = "x",
) -> ContextItem:
    metadata: dict = {"session_id": session_id}
    if embedding is not None:
        metadata["embedding"] = embedding
    if contains_named_third_party:
        metadata["contains_named_third_party"] = True
    return ContextItem(
        id=f"id_{session_id}_{timestamp or _now_iso()}",
        content=content,
        source="test",
        item_type="memory",
        memory_type=memory_type,
        timestamp=timestamp or _now_iso(),
        metadata=metadata,
    )


# Embedding pair with cosine similarity ~ 1.0 (identical vectors)
_VEC_HIGH_SIM = [1.0, 0.0, 0.0]
# Embedding pair with cosine similarity ~ 0.0 (orthogonal vectors)
_VEC_LOW_SIM = [0.0, 1.0, 0.0]


# ---------------------------------------------------------------------------
# detect_t2_pattern - happy path
# ---------------------------------------------------------------------------


def test_detect_returns_signal_when_all_thresholds_met() -> None:
    items = [
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s1"),
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s2"),
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s3"),
    ]
    signal = detect_t2_pattern(items, _VEC_HIGH_SIM)
    assert signal is not None
    assert signal.instance_count == 3
    assert signal.session_count == 3
    assert signal.has_named_party is False
    assert signal.max_similarity == pytest.approx(1.0)
    assert signal.category == "relational"


def test_has_named_party_true_when_any_candidate_has_flag() -> None:
    items = [
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s1"),
        _make_item(
            embedding=_VEC_HIGH_SIM, session_id="s2",
            contains_named_third_party=True,
        ),
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s3"),
    ]
    signal = detect_t2_pattern(items, _VEC_HIGH_SIM)
    assert signal is not None
    assert signal.has_named_party is True


def test_has_named_party_false_when_no_candidate_has_flag() -> None:
    items = [
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s1"),
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s2"),
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s3"),
    ]
    signal = detect_t2_pattern(items, _VEC_HIGH_SIM)
    assert signal is not None
    assert signal.has_named_party is False


# ---------------------------------------------------------------------------
# detect_t2_pattern - threshold failures
# ---------------------------------------------------------------------------


def test_detect_returns_none_below_min_instances() -> None:
    items = [
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s1"),
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s2"),
    ]
    assert detect_t2_pattern(items, _VEC_HIGH_SIM) is None


def test_detect_returns_none_below_similarity_threshold() -> None:
    items = [
        _make_item(embedding=_VEC_LOW_SIM, session_id="s1"),
        _make_item(embedding=_VEC_LOW_SIM, session_id="s2"),
        _make_item(embedding=_VEC_LOW_SIM, session_id="s3"),
    ]
    assert detect_t2_pattern(items, _VEC_HIGH_SIM) is None


def test_detect_returns_none_single_session() -> None:
    items = [
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s1"),
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s1"),
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s1"),
    ]
    assert detect_t2_pattern(items, _VEC_HIGH_SIM) is None


def test_detect_returns_none_no_recent_records() -> None:
    items = [
        _make_item(
            embedding=_VEC_HIGH_SIM, session_id="s1",
            timestamp=_old_iso(60),
        ),
        _make_item(
            embedding=_VEC_HIGH_SIM, session_id="s2",
            timestamp=_old_iso(60),
        ),
        _make_item(
            embedding=_VEC_HIGH_SIM, session_id="s3",
            timestamp=_old_iso(60),
        ),
    ]
    assert detect_t2_pattern(items, _VEC_HIGH_SIM) is None


def test_detect_returns_none_when_query_embedding_missing() -> None:
    items = [
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s1"),
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s2"),
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s3"),
    ]
    assert detect_t2_pattern(items, None) is None
    assert detect_t2_pattern(items, []) is None


def test_detect_skips_records_with_missing_embedding() -> None:
    items = [
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s1"),
        _make_item(embedding=_VEC_HIGH_SIM, session_id="s2"),
        # Third item lacks embedding metadata - should be skipped, dropping
        # the candidate count below T2_MIN_INSTANCES.
        _make_item(embedding=None, session_id="s3"),
    ]
    assert detect_t2_pattern(items, _VEC_HIGH_SIM) is None


def test_detect_excludes_non_conversation_types() -> None:
    items = [
        _make_item(
            embedding=_VEC_HIGH_SIM, session_id="s1",
            memory_type="reflection",
        ),
        _make_item(
            embedding=_VEC_HIGH_SIM, session_id="s2",
            memory_type="lodestone",
        ),
        _make_item(
            embedding=_VEC_HIGH_SIM, session_id="s3",
            memory_type="state",
        ),
    ]
    assert detect_t2_pattern(items, _VEC_HIGH_SIM) is None


def test_detect_returns_none_for_empty_input() -> None:
    assert detect_t2_pattern([], _VEC_HIGH_SIM) is None


def test_detect_constant_thresholds_match_adr() -> None:
    """Pin the ADR-021 hyperparameters at the constant-definition level
    so a future change to defaults is intentional."""
    assert T2_MIN_INSTANCES == 3
    assert T2_MIN_SESSIONS == 2
    assert T2_MIN_SIMILARITY == 0.82
    assert T2_RECENCY_DAYS == 30
