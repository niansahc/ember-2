"""
tests/test_context_quality.py

Tests for context quality tuning: identity query profile retrieval
and reflection junk filtering.
"""

import pytest

from src.context.retriever import ContextRetriever
from src.reflection.generate_reflection import _should_skip_for_reflection


class TestIdentityQueryDetection:
    """Identity queries should lower the profile score gate."""

    def test_who_am_i_is_identity(self):
        retriever = ContextRetriever()
        assert retriever._is_identity_query("Who am I?")

    def test_know_about_me_is_identity(self):
        retriever = ContextRetriever()
        assert retriever._is_identity_query("What do you know about me?")

    def test_about_myself_is_identity(self):
        retriever = ContextRetriever()
        assert retriever._is_identity_query("Tell me about myself")

    def test_describe_me_is_identity(self):
        retriever = ContextRetriever()
        assert retriever._is_identity_query("Describe me")

    def test_what_am_i_like_is_identity(self):
        retriever = ContextRetriever()
        assert retriever._is_identity_query("What am I like?")

    def test_normal_query_not_identity(self):
        retriever = ContextRetriever()
        assert not retriever._is_identity_query("What should I work on today?")

    def test_weather_not_identity(self):
        retriever = ContextRetriever()
        assert not retriever._is_identity_query("What's the weather like?")

    def test_case_insensitive(self):
        retriever = ContextRetriever()
        assert retriever._is_identity_query("WHO AM I")
        assert retriever._is_identity_query("what do you KNOW ABOUT ME")


class TestReflectionJunkFilter:
    """Assistant filler patterns should be caught by the skip filter."""

    def test_no_earlier_conversation(self):
        assert _should_skip_for_reflection(
            "there is no earlier conversation summary provided. this is the start."
        )

    def test_no_conversation_summary(self):
        assert _should_skip_for_reflection(
            "no conversation summary available for this session."
        )

    def test_seeking_clarity(self):
        assert _should_skip_for_reflection(
            "i understand you're seeking clarity without the need for reassurance."
        )

    def test_seeking_clarity_alternate(self):
        assert _should_skip_for_reflection(
            "i understand you are seeking a more direct approach."
        )

    def test_what_would_you_like(self):
        assert _should_skip_for_reflection(
            "what would you like to discuss or ask about today?"
        )

    def test_here_to_help(self):
        assert _should_skip_for_reflection(
            "i'm here to help you with whatever you need."
        )

    def test_how_can_i_assist(self):
        assert _should_skip_for_reflection(
            "how can i assist you today? let me know what's on your mind."
        )

    def test_short_i_can_filler(self):
        assert _should_skip_for_reflection("i can help you with that.")

    def test_short_i_understand_filler(self):
        assert _should_skip_for_reflection("i understand your concern about this.")

    def test_short_i_worked_not_filler(self):
        """Real user content starting with 'i ' should NOT be skipped."""
        assert not _should_skip_for_reflection(
            "i worked through the retrieval pipeline today and fixed a bug."
        )

    def test_multiple_i_sentences_assistant_voice(self):
        text = (
            "I understand your concern. I think we should look at this differently. "
            "I would suggest taking a step back. I believe the best approach is to wait."
        )
        assert _should_skip_for_reflection(text.lower())

    def test_real_user_content_not_skipped(self):
        assert not _should_skip_for_reflection(
            "i've been working on the retrieval pipeline for three days and i think "
            "the ranking weights need adjustment. the assistant responses are still "
            "contaminating the context packet."
        )

    def test_real_journal_not_skipped(self):
        assert not _should_skip_for_reflection(
            "today was a good day. got a lot done on the project. "
            "the installer is working and my partner tested it. "
            "feeling good about the direction."
        )
