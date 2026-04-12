"""
tests/test_bug009_topic_fixation.py

Tests for BUG-009: topic fixation.
Covers: topic decline detection, state resolution, retrieval
suppression, and sticky decline notes in prompt.
"""

import pytest
from pathlib import Path

from src.context.conversation_buffer import (
    ConversationBuffer,
    TOPIC_DECLINE_MARKERS,
)
from src.state.state_service import StateService
from src.state.models import StateRecord


class TestTopicDeclineDetection:
    """ConversationBuffer detects when the user declines a topic."""

    @pytest.mark.parametrize("message,expected_topic", [
        ("I don't want to talk about my job", "my job"),
        ("I don't want to discuss the project deadline", "the project deadline"),
        ("Drop it please", ""),  # "drop it" has no trailing topic
        ("Let's move on from this", "from this"),
        ("Can we change the subject", ""),
        ("Enough about the budget", "the budget"),
    ])
    def test_decline_detection(self, message, expected_topic):
        buf = ConversationBuffer()
        buf.add_turn(message, "Of course.")
        if expected_topic:
            assert len(buf.declined_topics) >= 1
            assert expected_topic in buf.declined_topics
        # "drop it" with no trailing content is too short (< 3 chars) and skipped

    def test_decline_persists_across_turns(self):
        buf = ConversationBuffer()
        buf.add_turn("I don't want to talk about my manager", "Understood.")
        buf.add_turn("Tell me a joke", "Why did the chicken...")
        assert "my manager" in buf.declined_topics

    def test_multiple_declines_accumulated(self):
        buf = ConversationBuffer()
        buf.add_turn("I don't want to discuss the budget", "Got it.")
        buf.add_turn("Enough about my health", "Moving on.")
        assert len(buf.declined_topics) >= 2

    def test_normal_messages_no_decline(self):
        buf = ConversationBuffer()
        buf.add_turn("Tell me about solar panels", "Solar panels...")
        assert buf.declined_topics == []

    def test_marker_list_is_nonempty(self):
        assert len(TOPIC_DECLINE_MARKERS) >= 5


class TestStateResolution:
    """StateService.resolve_open_loops_by_topic writes resolution records
    for matching open_loop records."""

    def _make_service(self, tmp_path: Path) -> StateService:
        state_dir = tmp_path / "memory" / "state"
        state_dir.mkdir(parents=True)
        return StateService(vault_path=tmp_path)

    def _write_open_loop(self, service: StateService, text: str) -> StateRecord:
        record = StateService.make_record(
            state_type="open_loop",
            text=text,
            source="test",
        )
        service.write(record)
        return record

    def test_resolves_matching_open_loop(self, tmp_path):
        svc = self._make_service(tmp_path)
        self._write_open_loop(svc, "Follow up on the budget review")
        count = svc.resolve_open_loops_by_topic("the budget")
        assert count == 1
        # The resolved record should be in the vault
        all_records = svc.read_all()
        resolved = [r for r in all_records if r.metadata.get("resolved")]
        assert len(resolved) == 1
        assert resolved[0].metadata["resolution"] == "user_declined"

    def test_does_not_resolve_unrelated_loops(self, tmp_path):
        svc = self._make_service(tmp_path)
        self._write_open_loop(svc, "Follow up on the budget review")
        self._write_open_loop(svc, "Schedule dentist appointment")
        count = svc.resolve_open_loops_by_topic("the budget")
        assert count == 1  # only the budget loop resolved

    def test_does_not_resolve_already_resolved(self, tmp_path):
        svc = self._make_service(tmp_path)
        record = StateService.make_record(
            state_type="open_loop",
            text="Budget already done",
            source="test",
            metadata={"resolved": True},
        )
        svc.write(record)
        count = svc.resolve_open_loops_by_topic("budget")
        assert count == 0

    def test_returns_zero_for_empty_topic(self, tmp_path):
        svc = self._make_service(tmp_path)
        self._write_open_loop(svc, "Something important")
        assert svc.resolve_open_loops_by_topic("") == 0
        assert svc.resolve_open_loops_by_topic("ab") == 0

    def test_case_insensitive_matching(self, tmp_path):
        svc = self._make_service(tmp_path)
        self._write_open_loop(svc, "Review the BUDGET proposal")
        count = svc.resolve_open_loops_by_topic("budget")
        assert count == 1


class TestRetrievalSuppression:
    """Declined topics should be filtered from vault_memory in the prompt."""

    def test_declined_topic_filtered_from_context(self):
        from src.llm.prompt_builder import PromptBuilder
        from src.context.models import ContextPacket, ContextItem

        pb = PromptBuilder()
        pb.conversation_buffer.add_turn(
            "I don't want to talk about my diet", "Understood."
        )

        items = [
            ContextItem(
                id="m1", content="User is tracking their diet progress.",
                source="conversation", item_type="conversation",
                memory_type="conversation", score=0.7,
            ),
            ContextItem(
                id="m2", content="User started a new coding project.",
                source="conversation", item_type="conversation",
                memory_type="conversation", score=0.6,
            ),
        ]
        packet = ContextPacket(user_message="What's new?", memory_items=items)
        section = pb._build_context_section(packet)

        assert "coding project" in section
        assert "diet" not in section

    def test_non_declined_topics_preserved(self):
        from src.llm.prompt_builder import PromptBuilder
        from src.context.models import ContextPacket, ContextItem

        pb = PromptBuilder()
        # No topics declined
        items = [
            ContextItem(
                id="m1", content="User likes hiking.",
                source="conversation", item_type="conversation",
                memory_type="conversation", score=0.7,
            ),
        ]
        packet = ContextPacket(user_message="Tell me something.", memory_items=items)
        section = pb._build_context_section(packet)
        assert "hiking" in section


class TestStickyDeclineNotes:
    """Decline notes should appear in the conversation history section."""

    def test_decline_note_in_prompt(self):
        from src.llm.prompt_builder import PromptBuilder
        from src.context.models import ContextPacket

        pb = PromptBuilder()
        pb.conversation_buffer.add_turn("Hello", "Hi there.")
        pb.conversation_buffer.add_turn(
            "I don't want to discuss the budget", "Moving on."
        )

        packet = ContextPacket(user_message="What should we do today?")
        prompt = pb.build_prompt(packet)

        assert "user has declined the topic" in prompt
        assert "budget" in prompt
        assert "Do not raise it again" in prompt

    def test_multiple_decline_notes(self):
        from src.llm.prompt_builder import PromptBuilder
        from src.context.models import ContextPacket

        pb = PromptBuilder()
        pb.conversation_buffer.add_turn(
            "I don't want to discuss the budget", "OK."
        )
        pb.conversation_buffer.add_turn(
            "Enough about my health", "Understood."
        )

        packet = ContextPacket(user_message="What's next?")
        prompt = pb.build_prompt(packet)

        assert "budget" in prompt
        assert "my health" in prompt
