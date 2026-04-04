"""
tests/test_task_prompt.py

Tests for task section rendering in prompts.
"""

import pytest
from unittest.mock import patch

from src.context.models import ContextPacket
from src.llm.prompt_builder import PromptBuilder
from src.tasks.models import TaskItem


@pytest.fixture
def builder():
    """Create a PromptBuilder with a minimal system prompt."""
    with patch("src.llm.prompt_builder.Path.read_text", return_value="You are Ember."):
        return PromptBuilder()


class TestTaskPromptSection:
    """Tests for _build_task_section in PromptBuilder."""

    def test_no_tasks_shows_none(self, builder):
        packet = ContextPacket(user_message="test")
        section = builder._build_task_section(packet)
        assert section == ""  # No tasks = empty section (no noise)

    def test_active_task_rendered(self, builder):
        packet = ContextPacket(
            user_message="test",
            task_items=[
                TaskItem(id="t1", title="Fix retrieval bug", status="active"),
            ],
        )
        section = builder._build_task_section(packet)
        assert "ACTIVE TASKS:" in section
        assert "- [active] Fix retrieval bug" in section

    def test_proposed_task_rendered(self, builder):
        packet = ContextPacket(
            user_message="test",
            task_items=[
                TaskItem(id="t1", title="Research caching", status="proposed"),
            ],
        )
        section = builder._build_task_section(packet)
        assert "- [proposed] Research caching" in section

    def test_priority_shown(self, builder):
        packet = ContextPacket(
            user_message="test",
            task_items=[
                TaskItem(id="t1", title="Ship v0.12", status="active", priority="high"),
            ],
        )
        section = builder._build_task_section(packet)
        assert "(priority: high)" in section

    def test_multiple_tasks_rendered(self, builder):
        packet = ContextPacket(
            user_message="test",
            task_items=[
                TaskItem(id="t1", title="Task A", status="active"),
                TaskItem(id="t2", title="Task B", status="proposed"),
                TaskItem(id="t3", title="Task C", status="active", priority="low"),
            ],
        )
        section = builder._build_task_section(packet)
        lines = section.split("\n")
        assert lines[0] == "ACTIVE TASKS:"
        assert len(lines) == 4  # header + 3 tasks

    def test_task_section_in_full_prompt(self, builder):
        packet = ContextPacket(
            user_message="What should I work on?",
            task_items=[
                TaskItem(id="t1", title="Fix the bug", status="active"),
            ],
        )
        prompt = builder.build_prompt(packet)
        assert "ACTIVE TASKS:" in prompt
        assert "Fix the bug" in prompt
        # Task section should appear after STATE and before reflections
        state_idx = prompt.index("<current_state>")
        task_idx = prompt.index("ACTIVE TASKS:")
        assert task_idx > state_idx


class TestCapabilitiesSection:
    """Tests for _build_capabilities_section in PromptBuilder."""

    def test_capabilities_content(self, builder):
        section = builder._build_capabilities_section()
        assert "CAPABILITIES:" in section
        assert "create tasks directly" in section
        assert "Do not tell the user to add tasks themselves" in section
        assert "Do not confirm task creation unless the write actually succeeded" in section

    def test_capabilities_in_full_prompt(self, builder):
        packet = ContextPacket(
            user_message="test",
            task_items=[
                TaskItem(id="t1", title="Some task", status="active"),
            ],
        )
        prompt = builder.build_prompt(packet)
        assert "CAPABILITIES:" in prompt
        # CAPABILITIES is in system prompt, ACTIVE TASKS is in context packet
        cap_idx = prompt.index("CAPABILITIES:")
        assert cap_idx > 0
