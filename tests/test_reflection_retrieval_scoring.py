"""
tests/test_reflection_retrieval_scoring.py

Recalling Too Well Phase 1, items 4-5: ContextRetriever.get_reflection_items()
must score reflections by real relevance to the query instead of a
hardcoded 1.0, and must not inject the most recent reflection when search
returns nothing regardless of topic.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.context.retriever import ContextRetriever


def _make_retriever(search_results: list[dict]) -> ContextRetriever:
    memory_service = MagicMock()
    memory_service.search.return_value = search_results
    # Any call to .read() here would be the removed unconditional fallback
    # -- fail loudly if get_reflection_items still calls it.
    memory_service.read.side_effect = AssertionError(
        "get_reflection_items must not call memory_service.read() -- "
        "the unconditional fallback was removed (item 5)"
    )
    return ContextRetriever(memory_service=memory_service)


class TestRealScore:
    def test_score_is_not_hardcoded_to_one(self):
        retriever = _make_retriever(
            [
                {
                    "id": "refl-1",
                    "text": "some tangentially related reflection content about weather",
                    "timestamp": "2026-09-01T00-00-00",
                    "tags": [],
                }
            ]
        )
        items = retriever.get_reflection_items("what did I say about the backlog project")
        assert len(items) == 1
        assert items[0].score != 1.0

    def test_higher_token_overlap_scores_higher(self):
        retriever = _make_retriever(
            [
                {
                    "id": "refl-close",
                    "text": "the backlog project kept coming up as a source of frustration this month",
                    "timestamp": "2026-09-01T00-00-00",
                    "tags": [],
                },
                {
                    "id": "refl-far",
                    "text": "the weather has been unusually warm and the garden needs attention",
                    "timestamp": "2026-09-01T00-00-00",
                    "tags": [],
                },
            ]
        )
        items = retriever.get_reflection_items("what did I say about the backlog project")
        by_id = {item.id: item.score for item in items}
        assert by_id["refl-close"] > by_id["refl-far"]

    def test_score_is_between_zero_and_one(self):
        retriever = _make_retriever(
            [
                {
                    "id": "refl-1",
                    "text": "said quite a bit about the backlog project over the last few weeks",
                    "timestamp": "2026-09-01T00-00-00",
                    "tags": [],
                }
            ]
        )
        items = retriever.get_reflection_items("what did I say about the backlog project")
        assert len(items) == 1
        assert 0.0 <= items[0].score <= 1.0


class TestNoUnconditionalFallback:
    def test_empty_search_yields_empty_items(self):
        retriever = _make_retriever([])
        items = retriever.get_reflection_items("anything at all")
        assert items == []
