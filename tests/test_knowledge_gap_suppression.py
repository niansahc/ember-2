"""
tests/test_knowledge_gap_suppression.py

Tests that the knowledge gap injection is suppressed for conversational
and emotional queries that don't need vault content.
"""

import pytest


class TestConversationalSuppression:
    """Emotional/social queries should NOT get the knowledge gap injection."""

    @pytest.mark.parametrize("message", [
        "I'm tired.",
        "How are you?",
        "That was a hard week.",
        "I'm exhausted.",
        "Good morning!",
        "Hey, how's it going?",
        "Thanks for that.",
        "I'm frustrated with this.",
    ])
    def test_emotional_queries_are_conversational(self, message):
        """Short emotional messages should be detected as conversational."""
        from src.api.openai_adapter import CONVERSATIONAL_MARKERS
        msg_lower = message.lower().strip()
        is_conv = any(m in msg_lower for m in CONVERSATIONAL_MARKERS) and len(msg_lower) < 100
        assert is_conv, f"Expected conversational: {message}"

    @pytest.mark.parametrize("message", [
        "What are my current projects?",
        "Explain how photosynthesis works.",
        "What did I say about my work last week?",
        "Who is the president of France?",
    ])
    def test_informational_queries_are_not_conversational(self, message):
        """Information-seeking queries should NOT be detected as conversational."""
        from src.api.openai_adapter import CONVERSATIONAL_MARKERS
        msg_lower = message.lower().strip()
        is_conv = any(m in msg_lower for m in CONVERSATIONAL_MARKERS) and len(msg_lower) < 100
        assert not is_conv, f"Should not be conversational: {message}"

    def test_long_emotional_message_not_suppressed(self):
        """A long message that happens to contain emotional markers should
        still get gap injection — it's likely asking for information."""
        from src.api.openai_adapter import CONVERSATIONAL_MARKERS
        msg = "I'm tired of debugging this retrieval pipeline. Can you look up the latest best practices for vector search optimization in personal AI systems?"
        msg_lower = msg.lower().strip()
        is_conv = any(m in msg_lower for m in CONVERSATIONAL_MARKERS) and len(msg_lower) < 100
        assert not is_conv
