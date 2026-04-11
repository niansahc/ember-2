"""
tests/test_profile_retrieval.py

Tests for profile retrieval via semantic search.
Verifies that get_profile_items() uses the vector index, not keyword matching,
and that identity query detection covers both user-directed and Ember-directed queries.
"""

import pytest
from unittest.mock import patch

from src.context.retriever import ContextRetriever


# ---------------------------------------------------------------------------
# Helpers — realistic mock profile records
# ---------------------------------------------------------------------------

MOCK_PROFILE_RECORDS = [
    {"id": "p1", "content": "My name is Jordan. I go by Jordan or J. I use he/him pronouns.", "score": 0.50, "memory_type": "profile", "metadata": {}},
    {"id": "p2", "content": "I work as a data engineer at a mid-size logistics company. Most of my day is SQL, Python, and pipeline orchestration.", "score": 0.33, "memory_type": "profile", "metadata": {}},
    {"id": "p3", "content": "I have a chronic back condition that flares up when I sit too long. I take breaks every 45 minutes and prefer standing desk setups.", "score": 0.26, "memory_type": "profile", "metadata": {}},
    {"id": "p4", "content": "My current side project is a home automation system built on a Raspberry Pi cluster. I am learning Rust for the firmware layer.", "score": 0.29, "memory_type": "profile", "metadata": {}},
    {"id": "p5", "content": "I keep a weekly journaling practice and spend Sunday mornings reviewing the previous week. It helps me notice patterns I miss in real time.", "score": 0.17, "memory_type": "profile", "metadata": {}},
    {"id": "p6", "content": "I prefer communication that gets to the point. I dislike small talk in work contexts and value directness over politeness.", "score": 0.27, "memory_type": "profile", "metadata": {}},
    {"id": "p7", "content": "What I want from an AI assistant: help me think through trade-offs, remember context across sessions, and challenge my assumptions when they are weak.", "score": 0.17, "memory_type": "profile", "metadata": {}},
    {"id": "p8", "content": "I live with a partner who works in education. We share a home office and coordinate schedules around meeting blocks.", "score": 0.15, "memory_type": "profile", "metadata": {}},
]


# ---------------------------------------------------------------------------
# Identity query detection
# ---------------------------------------------------------------------------

class TestIdentityQueryDetection:
    """_is_identity_query() must detect both user-directed and Ember-directed queries."""

    @pytest.mark.parametrize("query", [
        # User-directed: asking about themselves
        "what do you know about me",
        "who am I",
        "tell me about myself",
        "what have I told you about myself",
        "my profile",
        "describe me",
    ])
    def test_user_directed_identity_queries(self, query):
        retriever = ContextRetriever()
        assert retriever._is_identity_query(query) is True

    @pytest.mark.parametrize("query", [
        # Ember-directed: asking Ember about herself
        "tell me about yourself",
        "who are you",
        "what are you",
        "describe yourself",
        "tell me about ember",
        "who is ember",
    ])
    def test_ember_directed_identity_queries(self, query):
        retriever = ContextRetriever()
        assert retriever._is_identity_query(query) is True

    @pytest.mark.parametrize("query", [
        "what is the weather",
        "help me write code",
        "what should I focus on today",
        "how do I install docker",
        "what patterns have you noticed",
    ])
    def test_non_identity_queries_not_detected(self, query):
        retriever = ContextRetriever()
        assert retriever._is_identity_query(query) is False


# ---------------------------------------------------------------------------
# Profile retrieval routing and surfacing
# ---------------------------------------------------------------------------

class TestProfileRetrievalRoute:
    """get_profile_items() must route through semantic_search and surface multiple records."""

    def test_calls_semantic_search_not_memory_service(self):
        retriever = ContextRetriever()

        with patch("src.context.retriever._semantic_search", return_value=MOCK_PROFILE_RECORDS) as mock_sem:
            items = retriever.get_profile_items("who am I")
            mock_sem.assert_called_once_with(
                "who am I",
                memory_type="profile",
                limit=8,
                min_score=0.0,
            )

    def test_identity_query_surfaces_all_available_profiles(self):
        """An identity query should return all 8 profile records, not just the top 1."""
        retriever = ContextRetriever()

        with patch("src.context.retriever._semantic_search", return_value=MOCK_PROFILE_RECORDS):
            items = retriever.get_profile_items("tell me about yourself")
            assert len(items) == 8
            # Verify we got diverse content, not just the top-scoring record
            ids = {item.id for item in items}
            assert "p1" in ids  # name/pronouns
            assert "p2" in ids  # job
            assert "p3" in ids  # health
            assert "p5" in ids  # personal practice

    def test_ember_directed_query_triggers_full_profile(self):
        """'tell me about yourself' should trigger identity detection and get limit=8."""
        retriever = ContextRetriever()

        with patch("src.context.retriever._semantic_search", return_value=[]) as mock_sem:
            retriever.get_profile_items("tell me about yourself")
            mock_sem.assert_called_once_with(
                "tell me about yourself",
                memory_type="profile",
                limit=8,
                min_score=0.0,
            )

    def test_non_identity_query_uses_restricted_params(self):
        retriever = ContextRetriever()

        with patch("src.context.retriever._semantic_search", return_value=[]) as mock_sem:
            retriever.get_profile_items("what is the weather like")
            mock_sem.assert_called_once_with(
                "what is the weather like",
                memory_type="profile",
                limit=3,
                min_score=0.3,
            )


# ---------------------------------------------------------------------------
# Content filtering
# ---------------------------------------------------------------------------

class TestProfileContentFiltering:

    def test_short_content_filtered(self):
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
        retriever = ContextRetriever()
        mock_results = [
            {"id": "empty", "content": "", "score": 0.9, "memory_type": "profile", "metadata": {}},
        ]
        with patch("src.context.retriever._semantic_search", return_value=mock_results):
            items = retriever.get_profile_items("who am I")
            assert len(items) == 0


# ---------------------------------------------------------------------------
# Reflection junk filtering
# ---------------------------------------------------------------------------

class TestReflectionJunkFiltering:
    """_should_exclude_content() must catch file trees and session summary junk."""

    def test_file_tree_content_excluded(self):
        retriever = ContextRetriever()
        file_tree = (
            "Recent themes: User: Shorter messages please. | User: Okay it gave me a "
            "file structure clean up task \n\nsrc/\n\u251c\u2500\u2500 __init__.py\n"
            "\u251c\u2500\u2500 api/\n\u2502   \u251c\u2500\u2500 __init__.py"
        )
        assert retriever._should_exclude_content(file_tree, "tell me about yourself") is True

    def test_box_drawing_characters_excluded(self):
        retriever = ContextRetriever()
        # Just the box-drawing chars, no other markers
        assert retriever._should_exclude_content(
            "some content with \u2502 vertical line and \u251c branch chars in it for whatever reason",
            "anything"
        ) is True

    def test_recent_themes_prefix_excluded(self):
        retriever = ContextRetriever()
        assert retriever._should_exclude_content(
            "Recent themes: User: Shorter messages please. I've reminded you 5 times in this conversation.",
            "tell me about yourself"
        ) is True

    def test_legitimate_reflection_not_excluded(self):
        retriever = ContextRetriever()
        reflection = (
            "The user has been working intensively on Ember-2 development, "
            "logging 40 hours in four days. They expressed feeling worn out but "
            "not behind on work. Key themes: technical progress, work-life balance."
        )
        assert retriever._should_exclude_content(reflection, "what patterns have you noticed") is False
