"""
tests/test_profile_retrieval.py

Tests for profile retrieval via semantic search.
Verifies that get_profile_items() uses the vector index, not keyword matching.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.context.retriever import ContextRetriever


class TestProfileRetrievalRoute:
    """Verify get_profile_items() routes through semantic search."""

    def test_calls_semantic_search_not_memory_service(self):
        """get_profile_items() should call semantic_search, not MemoryService.search."""
        retriever = ContextRetriever()

        mock_results = [
            {
                "id": "test-1",
                "content": "My name is Test User. I am a software engineer with experience in Python.",
                "score": 0.85,
                "memory_type": "profile",
                "metadata": {},
            }
        ]

        with patch("src.context.retriever._semantic_search", return_value=mock_results) as mock_sem:
            items = retriever.get_profile_items("who am I")
            mock_sem.assert_called_once_with(
                "who am I",
                memory_type="profile",
                limit=8,  # identity query → limit=8
                min_score=0.0,  # identity query → min_score=0.0
            )
            assert len(items) == 1
            assert items[0].content == mock_results[0]["content"]
            assert items[0].memory_type == "profile"

    def test_non_identity_query_uses_higher_threshold(self):
        """Non-identity queries should use limit=3 and min_score=0.3."""
        retriever = ContextRetriever()

        with patch("src.context.retriever._semantic_search", return_value=[]) as mock_sem:
            retriever.get_profile_items("what is the weather like")
            mock_sem.assert_called_once_with(
                "what is the weather like",
                memory_type="profile",
                limit=3,
                min_score=0.3,
            )


class TestProfileScoreFiltering:
    """Verify score threshold and content filtering."""

    def test_short_content_filtered(self):
        """Records shorter than 40 characters should be excluded."""
        retriever = ContextRetriever()

        mock_results = [
            {"id": "short", "content": "Too short.", "score": 0.9, "memory_type": "profile", "metadata": {}},
            {"id": "ok", "content": "This is a long enough profile record to pass the 40-char minimum filter.", "score": 0.8, "memory_type": "profile", "metadata": {}},
        ]

        with patch("src.context.retriever._semantic_search", return_value=mock_results):
            items = retriever.get_profile_items("who am I")
            assert len(items) == 1
            assert items[0].id == "ok"

    def test_empty_content_filtered(self):
        """Records with empty content should be excluded."""
        retriever = ContextRetriever()

        mock_results = [
            {"id": "empty", "content": "", "score": 0.9, "memory_type": "profile", "metadata": {}},
        ]

        with patch("src.context.retriever._semantic_search", return_value=mock_results):
            items = retriever.get_profile_items("who am I")
            assert len(items) == 0


class TestIdentityQueryDetection:
    """Verify identity query patterns."""

    @pytest.mark.parametrize("query", [
        "what do you know about me",
        "who am I",
        "tell me about myself",
        "what have I told you about myself",
        "my profile",
    ])
    def test_identity_queries_detected(self, query):
        retriever = ContextRetriever()
        assert retriever._is_identity_query(query) is True

    @pytest.mark.parametrize("query", [
        "what is the weather",
        "help me write code",
        "what should I focus on today",
    ])
    def test_non_identity_queries_not_detected(self, query):
        retriever = ContextRetriever()
        assert retriever._is_identity_query(query) is False
