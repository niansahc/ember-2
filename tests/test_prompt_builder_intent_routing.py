"""tests/test_prompt_builder_intent_routing.py

Regression suite for is_conversational_query() and intent-class-driven
routing inside PromptBuilder.build_prompt().

Pins current behavior of:
  - CONVERSATIONAL_MARKERS matching (returns True)
  - Non-conversational intent queries (return False)
  - Length gate at 100 chars
  - Personal-vault gate (ZERO confidence block on personal-class +
    empty memory; neutral marker on conversational queries; non-personal
    neutral marker on general-knowledge queries)
  - State section rendering when state_items are present

These tests do not exercise live LLM or Ollama calls. PromptBuilder is
exercised end-to-end on a synthetic ContextPacket.
"""

from __future__ import annotations

import pytest

from src.context.models import ContextItem, ContextPacket
from src.state.models import StateItem
from src.llm.prompt_builder import PromptBuilder, is_conversational_query


# ---------------------------------------------------------------------------
# is_conversational_query() — markers that should match
# ---------------------------------------------------------------------------


class TestConversationalMarkers:
    """Each listed marker is in CONVERSATIONAL_MARKERS and must return True
    when the user message contains it (within the length gate)."""

    @pytest.mark.parametrize(
        "marker",
        [
            "good morning",
            "hey",
            "thanks",
            "how are you",
            "i'm tired",
            "i'm exhausted",
            "i'm frustrated",
            "i'm overwhelmed",
            "good night",
            "hi there",
        ],
    )
    def test_marker_returns_true(self, marker):
        assert is_conversational_query(marker) is True


# ---------------------------------------------------------------------------
# is_conversational_query() — non-conversational queries
# ---------------------------------------------------------------------------


class TestNonConversationalIntents:
    """Representative queries for non-conversational intent classes
    (factual_recall, web_search, recent, status_state). None contain a
    conversational marker, so all must return False."""

    @pytest.mark.parametrize(
        "query",
        [
            "what is the capital of France",
            "search for recent AI news",
            "what have I been working on lately",
            "what's my current focus",
        ],
    )
    def test_non_conversational_query_returns_false(self, query):
        assert is_conversational_query(query) is False


# ---------------------------------------------------------------------------
# is_conversational_query() — edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:

    def test_empty_string_returns_false(self):
        assert is_conversational_query("") is False

    def test_single_char_returns_false(self):
        assert is_conversational_query("?") is False

    def test_short_marker_in_prefix_returns_true(self):
        """A query that begins with a conversational greeting and adds a
        short factual continuation still matches when the total length
        stays under the 100-char gate. Documents current behavior:
        markers in short prefixes are treated as conversational."""
        query = "good morning, what's the weather in Tokyo"
        assert len(query) < 100
        assert is_conversational_query(query) is True

    def test_length_gate_blocks_long_query_with_marker(self):
        """A query containing a conversational marker but >= 100 chars
        is treated as a substantive question, not a greeting. The length
        gate at prompt_builder.py is the boundary."""
        query = (
            "good morning, can you summarize the population, capital, "
            "currency, and primary industries of France for me please"
        )
        assert len(query) >= 100
        assert is_conversational_query(query) is False

    def test_curly_apostrophe_normalized(self):
        """Mobile keyboards autocorrect "I'm" to "I’m". The function
        normalizes U+2019 to a straight quote before matching, so curly
        forms still trigger CONVERSATIONAL_MARKERS."""
        assert is_conversational_query("I’m tired") is True


# ---------------------------------------------------------------------------
# build_prompt() routing — integration tests
# ---------------------------------------------------------------------------


def _make_packet(
    user_message: str,
    memory_items=None,
    state_items=None,
    reflection_items=None,
) -> ContextPacket:
    """Minimal ContextPacket builder for routing-test fixtures."""
    return ContextPacket(
        user_message=user_message,
        memory_items=memory_items or [],
        state_items=state_items or [],
        reflection_items=reflection_items or [],
    )


def _make_state_item(text: str, category: str = "current_focus") -> StateItem:
    return StateItem(
        category=category,
        text=text,
        timestamp="2026-04-30T12-00-00",
    )


class TestBuildPromptRouting:
    """End-to-end checks that intent_class drives the right context
    section content. No production code changes; these pin behavior."""

    def test_factual_recall_empty_memory_renders_zero_block(self):
        """factual_recall is in _PERSONAL_INTENT_CLASSES, so an empty
        memory packet on this intent renders the ZERO confidence block.
        Pins current behavior; the docstring at prompt_builder.py:936-941
        and the gate at line 953 are the canonical reference."""
        pb = PromptBuilder()
        packet = _make_packet(user_message="what was my second goal")
        rendered = pb.build_prompt(packet, intent_class="factual_recall")
        assert "ZERO" in rendered
        assert "[Retrieval confidence:]" in rendered

    def test_status_state_renders_state_section(self):
        """When state_items are present, the assembled prompt must
        include the <current_state> section with the item text."""
        pb = PromptBuilder()
        focus_text = "active focus: shipping v0.18.0 hardening"
        packet = _make_packet(
            user_message="what's my current focus",
            state_items=[_make_state_item(focus_text)],
        )
        rendered = pb.build_prompt(packet, intent_class="status_state")
        assert "<current_state>" in rendered
        assert focus_text in rendered

    def test_conversational_query_no_personal_vault_gate(self):
        """A conversational greeting with empty memory must render the
        conversational neutral marker, not the ZERO block, and must not
        emit the KNOWLEDGE_GAP_LINE phrase ('I don't have that in my
        memory'). Note: '[Retrieval confidence:]' appears in instruction
        rules regardless of memory state, so the ZERO check looks for
        the distinctive 'confidence: ZERO' substring instead."""
        pb = PromptBuilder()
        packet = _make_packet(user_message="good morning")
        rendered = pb.build_prompt(packet, intent_class="default")
        assert "(conversational)" in rendered
        assert "confidence: ZERO" not in rendered
        assert "I don't have that in my memory" not in rendered

    def test_general_knowledge_query_renders_neutral_marker(self):
        """A non-personal intent class with empty memory must render the
        neutral 'No retrieved memory for this query.' marker, not ZERO.
        Path: _is_personal_query returns False (intent not in personal
        classes; no \\bmy\\s+ in message)."""
        pb = PromptBuilder()
        packet = _make_packet(user_message="what is the speed of light")
        rendered = pb.build_prompt(packet, intent_class="default")
        assert "No retrieved memory for this query" in rendered
        assert "confidence: ZERO" not in rendered
