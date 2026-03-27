"""
tests/test_project_boost.py

Tests for project-scoped retrieval boost (ADR-007).
"""

import pytest
from src.context.ranker import ContextRanker
from src.context.models import ContextItem


def make_item(content="test content", score=0.5, metadata=None):
    """Create a ContextItem for testing."""
    return ContextItem(
        id="test-id",
        content=content,
        source="test",
        item_type="conversation",
        score=score,
        metadata=metadata or {},
    )


class TestProjectBoostMatching:
    """Items with matching project_id should get boosted."""

    def test_matching_project_id_gets_boosted(self):
        ranker = ContextRanker()
        item = make_item(score=0.5, metadata={"project_id": "proj_abc"})
        result = ranker.apply_project_boost([item], "proj_abc")
        assert result[0].score == pytest.approx(0.65)

    def test_non_matching_project_id_unchanged(self):
        ranker = ContextRanker()
        item = make_item(score=0.5, metadata={"project_id": "proj_other"})
        result = ranker.apply_project_boost([item], "proj_abc")
        assert result[0].score == pytest.approx(0.5)

    def test_no_project_id_in_metadata_unchanged(self):
        ranker = ContextRanker()
        item = make_item(score=0.5, metadata={"role": "user"})
        result = ranker.apply_project_boost([item], "proj_abc")
        assert result[0].score == pytest.approx(0.5)

    def test_empty_metadata_unchanged(self):
        ranker = ContextRanker()
        item = make_item(score=0.5, metadata={})
        result = ranker.apply_project_boost([item], "proj_abc")
        assert result[0].score == pytest.approx(0.5)

    def test_mixed_items_only_matching_boosted(self):
        ranker = ContextRanker()
        items = [
            make_item(content="in project", score=0.4, metadata={"project_id": "proj_abc"}),
            make_item(content="not in project", score=0.6, metadata={"project_id": "proj_other"}),
            make_item(content="no project", score=0.5, metadata={}),
        ]
        result = ranker.apply_project_boost(items, "proj_abc")
        assert result[0].score == pytest.approx(0.55)  # 0.4 + 0.15
        assert result[1].score == pytest.approx(0.6)    # unchanged
        assert result[2].score == pytest.approx(0.5)    # unchanged


class TestProjectBoostNone:
    """Passing project_id=None should return items unchanged."""

    def test_none_project_returns_unchanged(self):
        ranker = ContextRanker()
        item = make_item(score=0.5, metadata={"project_id": "proj_abc"})
        result = ranker.apply_project_boost([item], None)
        assert result[0].score == pytest.approx(0.5)

    def test_empty_string_project_returns_unchanged(self):
        ranker = ContextRanker()
        item = make_item(score=0.5, metadata={"project_id": "proj_abc"})
        result = ranker.apply_project_boost([item], "")
        assert result[0].score == pytest.approx(0.5)

    def test_empty_items_returns_empty(self):
        ranker = ContextRanker()
        result = ranker.apply_project_boost([], "proj_abc")
        assert result == []


class TestBoostValue:
    """The boost should be exactly 0.15."""

    def test_boost_is_015(self):
        ranker = ContextRanker()
        item = make_item(score=0.0, metadata={"project_id": "proj_x"})
        result = ranker.apply_project_boost([item], "proj_x")
        assert result[0].score == pytest.approx(0.15)

    def test_boost_is_additive(self):
        """Boost adds to existing score, doesn't replace it."""
        ranker = ContextRanker()
        item = make_item(score=0.8, metadata={"project_id": "proj_x"})
        result = ranker.apply_project_boost([item], "proj_x")
        assert result[0].score == pytest.approx(0.95)
