"""
tests/test_bug008_parenthetical_questions.py

Tests for BUG-008: repetitive parenthetical questions.
Covers: question objection detection, sticky suppression flag,
post-generation parenthetical filter, and sticky notes in prompt.
"""

import pytest

from src.context.conversation_buffer import (
    ConversationBuffer,
    QUESTION_OBJECTION_MARKERS,
)
from src.llm.adapter import strip_trailing_parenthetical_question


class TestQuestionObjectionDetection:
    """The conversation buffer detects when the user objects to questions."""

    @pytest.mark.parametrize("message", [
        "Stop asking me questions.",
        "Don't ask me things at the end.",
        "Please don't ask questions.",
        "Enough questions already.",
        "stop ending with questions please",
        "No more questions.",
        "quit asking me stuff",
    ])
    def test_objection_sets_suppression_flag(self, message):
        buf = ConversationBuffer()
        assert buf.question_suppressed is False
        buf.add_turn(message, "Understood.")
        assert buf.question_suppressed is True

    def test_flag_persists_across_turns(self):
        buf = ConversationBuffer()
        buf.add_turn("Stop asking questions.", "Understood.")
        assert buf.question_suppressed is True
        buf.add_turn("What time is it?", "It's 3pm.")
        assert buf.question_suppressed is True  # sticky

    def test_normal_messages_do_not_set_flag(self):
        buf = ConversationBuffer()
        buf.add_turn("Tell me about solar panels.", "Solar panels convert sunlight...")
        buf.add_turn("How do they work?", "They use photovoltaic cells...")
        assert buf.question_suppressed is False

    def test_marker_list_is_nonempty(self):
        assert len(QUESTION_OBJECTION_MARKERS) >= 5


class TestTrailingParentheticalFilter:
    """Post-generation filter strips trailing parenthetical questions."""

    def test_strips_trailing_parenthetical_question(self):
        text = "Here is my answer. (Is there anything else you'd like to explore?)"
        assert strip_trailing_parenthetical_question(text) == "Here is my answer."

    def test_strips_with_leading_whitespace(self):
        text = "Answer here.  (Would you like to know more?)"
        assert strip_trailing_parenthetical_question(text) == "Answer here."

    def test_preserves_interior_parenthetical_questions(self):
        """Interior parenthetical questions are content, not engagement fluff."""
        text = "The function (does it handle nulls?) processes the data cleanly."
        assert strip_trailing_parenthetical_question(text) == text

    def test_preserves_trailing_parenthetical_non_question(self):
        """Trailing parenthetical WITHOUT a question mark is not stripped."""
        text = "This is the answer. (See page 42 for details.)"
        assert strip_trailing_parenthetical_question(text) == text

    def test_preserves_no_parenthetical(self):
        text = "Just a clean response with no questions."
        assert strip_trailing_parenthetical_question(text) == text

    def test_strips_complex_parenthetical_question(self):
        text = "That covers the main points. (What aspects would you like me to dig into further?)"
        result = strip_trailing_parenthetical_question(text)
        assert result == "That covers the main points."

    def test_empty_string(self):
        assert strip_trailing_parenthetical_question("") == ""

    def test_only_parenthetical_question(self):
        text = "(Shall we continue?)"
        assert strip_trailing_parenthetical_question(text) == ""


class TestStickyNotesInPrompt:
    """Sticky notes should appear in the conversation history section."""

    def test_question_suppression_note_in_prompt(self):
        from src.llm.prompt_builder import PromptBuilder
        from src.context.models import ContextPacket

        pb = PromptBuilder()
        pb.conversation_buffer.add_turn("Hello", "Hi there.")
        pb.conversation_buffer.add_turn("Stop asking me questions.", "Understood.")

        packet = ContextPacket(user_message="Tell me about the weather.")
        prompt = pb.build_prompt(packet)

        assert "user has requested no questions" in prompt
        assert "Do not end responses with questions" in prompt
