"""
tests/test_ai_doc_quarantine.py

Tests for the AI documentation quarantine filter that prevents
identity contamination from web search results about other AI
systems (Claude, GPT, Gemini, etc.).
"""

import pytest

from src.context.service import _quarantine_ai_docs, AI_SYSTEM_NAMES, AI_DOC_MARKERS


class TestQuarantineFilter:
    """_quarantine_ai_docs splits web results into safe and quarantined."""

    def test_clean_results_pass_through(self):
        items = [
            {"title": "Bitcoin hits $60k", "url": "https://news.example.com", "snippet": "Bitcoin surged today."},
            {"title": "NBA Playoffs", "url": "https://sports.example.com", "snippet": "Lakers beat Celtics 112-108."},
        ]
        safe, quarantined = _quarantine_ai_docs(items, "What's the price of Bitcoin?")
        assert len(safe) == 2
        assert len(quarantined) == 0

    def test_quarantines_claude_documentation(self):
        items = [
            {"title": "Claude Model Documentation - Anthropic", "url": "https://docs.anthropic.com", "snippet": "Claude Sonnet has a training cutoff of May 2025."},
        ]
        safe, quarantined = _quarantine_ai_docs(items, "What is your training cutoff?")
        assert len(safe) == 0
        assert len(quarantined) == 1

    def test_quarantines_on_two_ai_names(self):
        items = [
            {"title": "GPT vs Claude comparison", "url": "https://example.com", "snippet": "OpenAI's GPT-4 and Anthropic's Claude compared."},
        ]
        safe, quarantined = _quarantine_ai_docs(items, "AI comparison")
        assert len(quarantined) == 1

    def test_quarantines_on_doc_marker(self):
        items = [
            {"title": "Model Specs", "url": "https://example.com", "snippet": "The context window is 128k tokens."},
        ]
        safe, quarantined = _quarantine_ai_docs(items, "How big is the context?")
        assert len(quarantined) == 1

    def test_quarantines_training_cutoff_marker(self):
        items = [
            {"title": "AI Updates", "url": "https://example.com", "snippet": "The knowledge cutoff for this model is April 2025."},
        ]
        safe, quarantined = _quarantine_ai_docs(items, "When was your training cutoff?")
        assert len(quarantined) == 1

    def test_single_ai_name_without_doc_marker_passes(self):
        """A news article mentioning one AI system name in passing
        should NOT be quarantined — only documentation-style content."""
        items = [
            {"title": "Tech News", "url": "https://example.com", "snippet": "OpenAI released a new product today."},
        ]
        safe, quarantined = _quarantine_ai_docs(items, "What's new in tech?")
        assert len(safe) == 1
        assert len(quarantined) == 0

    def test_mixed_results_split_correctly(self):
        items = [
            {"title": "Weather", "url": "https://weather.com", "snippet": "Sunny and 75F."},
            {"title": "Claude API Docs", "url": "https://docs.anthropic.com", "snippet": "Claude's training cutoff is May 2025."},
            {"title": "Sports", "url": "https://espn.com", "snippet": "Game results."},
        ]
        safe, quarantined = _quarantine_ai_docs(items, "What's the weather?")
        assert len(safe) == 2
        assert len(quarantined) == 1
        assert quarantined[0]["title"] == "Claude API Docs"


class TestEscapeHatch:
    """When the user explicitly asks about another AI system, quarantine
    is bypassed — the user wants that information."""

    def test_explicit_claude_inquiry_bypasses_quarantine(self):
        items = [
            {"title": "Claude Docs", "url": "https://docs.anthropic.com", "snippet": "Claude has a training cutoff of May 2025."},
        ]
        safe, quarantined = _quarantine_ai_docs(items, "Tell me about Claude's capabilities")
        assert len(safe) == 1
        assert len(quarantined) == 0

    def test_compare_query_bypasses_quarantine(self):
        items = [
            {"title": "GPT vs Claude", "url": "https://example.com", "snippet": "Comparing GPT-4 and Claude Sonnet."},
        ]
        safe, quarantined = _quarantine_ai_docs(items, "Compare GPT and Claude for coding tasks")
        assert len(safe) == 1
        assert len(quarantined) == 0

    def test_non_explicit_query_does_not_bypass(self):
        items = [
            {"title": "Claude Docs", "url": "https://docs.anthropic.com", "snippet": "Claude's training cutoff."},
        ]
        safe, quarantined = _quarantine_ai_docs(items, "What is your training cutoff?")
        assert len(quarantined) == 1


class TestSelfKnowledgeBoundary:
    """The self-knowledge boundary instruction must appear in the prompt."""

    def test_boundary_in_prompt(self):
        from src.llm.prompt_builder import PromptBuilder
        from src.context.models import ContextPacket
        pb = PromptBuilder()
        prompt = pb.build_prompt(ContextPacket(user_message="What model are you?"))
        assert "SELF-KNOWLEDGE BOUNDARY" in prompt
        assert "not authoritative for facts about Ember" in prompt
        assert "Do not adopt" in prompt

    def test_boundary_mentions_training_cutoff(self):
        from src.llm.prompt_builder import PromptBuilder
        prompt = PromptBuilder._build_self_knowledge_boundary()
        assert "training data cutoff" in prompt
        assert "say you don't know" in prompt


class TestConstants:
    """Verify quarantine constants are populated."""

    def test_ai_system_names_populated(self):
        assert len(AI_SYSTEM_NAMES) >= 10
        assert "claude" in AI_SYSTEM_NAMES
        assert "openai" in AI_SYSTEM_NAMES

    def test_ai_doc_markers_populated(self):
        assert len(AI_DOC_MARKERS) >= 5
        assert "training cutoff" in AI_DOC_MARKERS
        assert "context window" in AI_DOC_MARKERS
