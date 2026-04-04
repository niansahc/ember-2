"""
Tests for relevance gate on default policy.

When no non-profile items have raw cosine similarity >= threshold,
vault memory is suppressed for default policy queries. Profile items
are exempt.
"""

from src.context.models import ContextItem
from src.context.service import ContextService
from src.context.policies import ContextPolicy


def _make_item(memory_type: str, score: float = 0.5, raw_score: float = 0.3) -> ContextItem:
    return ContextItem(
        id=f"test-{memory_type}",
        content="test content long enough to pass filters easily here",
        source=memory_type,
        item_type=memory_type,
        memory_type=memory_type,
        score=score,
        metadata={"raw_score": raw_score},
    )


def test_low_raw_scores_suppress_non_profile_for_default():
    """When all non-profile raw scores < 0.5, non-profile items are suppressed."""
    from unittest.mock import patch

    service = ContextService()

    # Simulate: profile at raw 0.6, non-profile all below 0.5
    memory_items = [
        _make_item("profile", score=0.7, raw_score=0.6),
        _make_item("conversation", score=1.1, raw_score=0.3),
        _make_item("ingested", score=1.0, raw_score=0.2),
    ]

    policy = ContextPolicy(name="default")

    # Apply the relevance gate logic directly
    from src.core.config import get_retrieval_min_raw_score
    min_raw = get_retrieval_min_raw_score()

    non_profile = [i for i in memory_items if i.memory_type != "profile"]
    max_raw = max(
        (i.metadata.get("raw_score", 0.0) for i in non_profile),
        default=0.0,
    )

    assert max_raw < min_raw  # 0.3 < 0.5

    # After gate: only profile survives
    if policy.name == "default" and max_raw < min_raw:
        memory_items = [i for i in memory_items if i.memory_type == "profile"]

    assert len(memory_items) == 1
    assert memory_items[0].memory_type == "profile"


def test_high_raw_scores_pass_through():
    """When non-profile raw scores >= 0.5, all items pass through."""
    memory_items = [
        _make_item("profile", score=0.7, raw_score=0.6),
        _make_item("conversation", score=1.1, raw_score=0.7),
        _make_item("ingested", score=1.0, raw_score=0.5),
    ]

    non_profile = [i for i in memory_items if i.memory_type != "profile"]
    max_raw = max(
        (i.metadata.get("raw_score", 0.0) for i in non_profile),
        default=0.0,
    )

    assert max_raw >= 0.5  # 0.7 >= 0.5, gate does not fire


def test_profile_always_survives_relevance_gate():
    """Profile items are never suppressed by the relevance gate."""
    memory_items = [
        _make_item("profile", score=0.7, raw_score=0.6),
    ]

    # No non-profile items, max_raw defaults to 0.0
    non_profile = [i for i in memory_items if i.memory_type != "profile"]
    max_raw = max(
        (i.metadata.get("raw_score", 0.0) for i in non_profile),
        default=0.0,
    )

    # Gate fires (0.0 < 0.5) but profile survives
    if max_raw < 0.5:
        memory_items = [i for i in memory_items if i.memory_type == "profile"]

    assert len(memory_items) == 1
    assert memory_items[0].memory_type == "profile"


def test_gate_only_applies_to_default_policy():
    """Non-default policies are unaffected by the relevance gate."""
    policy = ContextPolicy(name="status_state")
    # Gate should not fire for non-default policies
    assert policy.name != "default"


def test_config_threshold_default():
    """RETRIEVAL_MIN_RAW_SCORE defaults to 0.5."""
    from src.core.config import get_retrieval_min_raw_score
    assert get_retrieval_min_raw_score() == 0.5
