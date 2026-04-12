"""
tests/test_knowledge_gap_suppression.py

Tests that the knowledge gap injection is suppressed for conversational
and emotional queries that don't need vault content. Covers both the
openai_adapter gap injection path and the prompt_builder system prompt
/ vault_memory framing paths — all three were emitting "I don't have
that in my memory" instructions prior to the full fix.
"""

import pytest

from src.context.models import ContextPacket
from src.llm.prompt_builder import (
    CONVERSATIONAL_MARKERS,
    PromptBuilder,
    is_conversational_query,
)


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
        assert is_conversational_query(message), f"Expected conversational: {message}"

    @pytest.mark.parametrize("message", [
        "What are my current projects?",
        "Explain how photosynthesis works.",
        "What did I say about my work last week?",
        "Who is the president of France?",
    ])
    def test_informational_queries_are_not_conversational(self, message):
        """Information-seeking queries should NOT be detected as conversational."""
        assert not is_conversational_query(message), f"Should not be conversational: {message}"

    def test_long_emotional_message_not_suppressed(self):
        """A long message that happens to contain emotional markers should
        still get gap injection — it's likely asking for information."""
        msg = "I'm tired of debugging this retrieval pipeline. Can you look up the latest best practices for vector search optimization in personal AI systems?"
        assert not is_conversational_query(msg)


class TestCurlyApostropheNormalization:
    """Mobile keyboards autocorrect ' to \u2019 (U+2019, right single quote).
    The marker list uses straight quotes, so without normalization the
    curly-apostrophe form slips past the check and receives gap injection."""

    @pytest.mark.parametrize("message", [
        "I\u2019m tired.",
        "I\u2019m exhausted.",
        "I\u2019m frustrated with this.",
        "What\u2019s up?",
        "How\u2019s it going?",
    ])
    def test_curly_apostrophe_messages_are_conversational(self, message):
        assert is_conversational_query(message), f"Curly apostrophe form missed: {message!r}"

    @pytest.mark.parametrize("message", [
        "I\u2018m tired.",  # left single quote variant (rarer but valid)
    ])
    def test_left_single_quote_messages_are_conversational(self, message):
        assert is_conversational_query(message), f"Left quote form missed: {message!r}"

    def test_marker_list_export_still_available(self):
        """Backward compat: the CONVERSATIONAL_MARKERS tuple is re-exported
        from src.api.openai_adapter for any external callers that import
        from the old location."""
        from src.api.openai_adapter import CONVERSATIONAL_MARKERS as EXPORT
        assert EXPORT is CONVERSATIONAL_MARKERS


class TestSystemPromptSuppression:
    """Even when the openai_adapter gap injection is skipped, the system
    prompt (AUTHORITY_RULES) and the vault_memory empty-state section
    both used to emit 'say "I don't have that in my memory"' instructions
    unconditionally. Q11/Q12 regression: "How are you?" still fired
    because these instructions reached the model regardless. All three
    paths must suppress together."""

    def test_authority_rules_omit_knowledge_gap_line_for_conversational(self):
        pb = PromptBuilder()
        packet = ContextPacket(user_message="I'm tired", memory_items=[])
        prompt = pb.build_prompt(packet)
        # The knowledge-gap instruction line must be gone.
        assert "I don't have that in my memory" not in prompt

    def test_authority_rules_include_knowledge_gap_line_for_informational(self):
        pb = PromptBuilder()
        packet = ContextPacket(user_message="What did I say about my project?", memory_items=[])
        prompt = pb.build_prompt(packet)
        # Informational query with empty vault — the instruction should still be present.
        assert "I don't have that in my memory" in prompt

    def test_vault_memory_empty_state_neutral_for_conversational(self):
        pb = PromptBuilder()
        packet = ContextPacket(user_message="How are you?", memory_items=[])
        section = pb._build_context_section(packet, is_conversational=True)
        assert "I don't have that in my memory" not in section
        assert "conversational" in section.lower() or "no retrieved memory" in section.lower()

    def test_vault_memory_empty_state_uses_gap_framing_for_informational(self):
        pb = PromptBuilder()
        packet = ContextPacket(user_message="What are my projects?", memory_items=[])
        section = pb._build_context_section(packet, is_conversational=False)
        assert "I don't have that in my memory" in section

    def test_curly_apostrophe_i_m_tired_triggers_system_prompt_suppression(self):
        """End-to-end: a curly-apostrophe 'I\u2019m tired' must produce a
        system prompt with no knowledge-gap framing. Regression for the
        Q11 failure where mobile-typed messages leaked past the check."""
        pb = PromptBuilder()
        packet = ContextPacket(user_message="I\u2019m tired", memory_items=[])
        prompt = pb.build_prompt(packet)
        assert "I don't have that in my memory" not in prompt

    def test_how_are_you_triggers_system_prompt_suppression(self):
        """Q12 regression: 'How are you?' has no apostrophe but was still
        failing because the system prompt unconditionally told Ember to
        say 'I don't have that in my memory' when vault was empty."""
        pb = PromptBuilder()
        packet = ContextPacket(user_message="How are you?", memory_items=[])
        prompt = pb.build_prompt(packet)
        assert "I don't have that in my memory" not in prompt
