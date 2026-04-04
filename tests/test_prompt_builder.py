"""
tests/test_prompt_builder.py

Tests for prompt assembly — verifies that the prompt sent to the model
contains correct labels, instructions, and section structure.
"""

import pytest

from src.context.models import ContextItem, ContextPacket
from src.llm.prompt_builder import PromptBuilder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile_item(content: str, id: str = "p1") -> ContextItem:
    return ContextItem(
        id=id, content=content, source="profile", item_type="profile",
        memory_type="profile", score=0.5,
    )


def _make_memory_item(content: str, item_type: str = "ingested", id: str = "m1") -> ContextItem:
    return ContextItem(
        id=id, content=content, source=item_type, item_type=item_type,
        memory_type=item_type, score=0.8,
    )


# ---------------------------------------------------------------------------
# Profile section label
# ---------------------------------------------------------------------------

class TestProfileSectionLabel:
    """The profile label must tell the model these are facts about the user,
    not about Ember. If reverted to 'User self-description', the model
    merges the user's identity with Ember's."""

    def test_profile_label_identifies_user_not_ember(self):
        pb = PromptBuilder()
        packet = ContextPacket(
            user_message="tell me about yourself",
            memory_items=[_make_profile_item("My name is Chas. I am a BSA.")],
        )
        prompt = pb._build_context_section(packet)
        assert "person Ember is talking to" in prompt

    def test_profile_label_does_not_say_user_self_description(self):
        """Regression guard — old label caused identity confusion."""
        pb = PromptBuilder()
        packet = ContextPacket(
            user_message="who are you",
            memory_items=[_make_profile_item("My name is Chas. I am a BSA.")],
        )
        prompt = pb._build_context_section(packet)
        assert "User self-description" not in prompt


# ---------------------------------------------------------------------------
# Identity instruction rule
# ---------------------------------------------------------------------------

class TestIdentityInstructionRule:
    """The instruction section must tell the model to answer as Ember
    when asked about itself, not adopt the user's profile."""

    def test_instruction_contains_identity_rule(self):
        pb = PromptBuilder()
        instructions = pb._build_instruction_section()
        assert "answer as Ember" in instructions
        assert "vault_memory describes the person you are talking to, not yourself" in instructions

    def test_instruction_contains_identity_rule_in_behavior_rules(self):
        pb = PromptBuilder()
        instructions = pb._build_instruction_section()
        assert "BEHAVIOR RULES:" in instructions
        # The identity rule should be inside the behavior rules block
        lines = instructions.split("\n")
        behavior_start = next(i for i, l in enumerate(lines) if "BEHAVIOR RULES:" in l)
        behavior_lines = lines[behavior_start:]
        identity_lines = [l for l in behavior_lines if "answer as Ember" in l]
        assert len(identity_lines) == 1


# ---------------------------------------------------------------------------
# Section structure — profile and non-profile items are separated
# ---------------------------------------------------------------------------

class TestContextSectionStructure:

    def test_profile_and_other_items_in_separate_sections(self):
        pb = PromptBuilder()
        packet = ContextPacket(
            user_message="hello",
            memory_items=[
                _make_profile_item("My name is Chas. I am a Business Systems Analyst in AI."),
                _make_memory_item("User: I'd like to tell you something about yourself"),
            ],
        )
        prompt = pb._build_context_section(packet)
        # Both sub-sections should be present
        assert "[About the person Ember is talking to:]" in prompt
        assert "[Retrieved memory:]" in prompt

    def test_profile_only_packet_has_no_context_section(self):
        pb = PromptBuilder()
        packet = ContextPacket(
            user_message="who am I",
            memory_items=[
                _make_profile_item("My name is Chas. I go by Chas."),
            ],
        )
        prompt = pb._build_context_section(packet)
        assert "[About the person Ember is talking to:]" in prompt
        assert "[Retrieved memory:]" not in prompt

    def test_empty_memory_shows_absence_signal(self):
        pb = PromptBuilder()
        packet = ContextPacket(user_message="hello", memory_items=[])
        prompt = pb._build_context_section(packet)
        assert "No relevant memory found for this query" in prompt
        assert "I don't have that in my memory" in prompt


class TestDateSection:
    """The date section should include day of week, time of day, and natural date."""

    def test_date_includes_day_of_week(self):
        pb = PromptBuilder()
        date_str = pb._build_date_section()
        days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
        assert any(day in date_str for day in days)

    def test_date_includes_time_of_day(self):
        pb = PromptBuilder()
        date_str = pb._build_date_section()
        times = ("morning", "afternoon", "evening", "late night")
        assert any(t in date_str for t in times)

    def test_date_format_is_natural_language(self):
        pb = PromptBuilder()
        date_str = pb._build_date_section()
        assert date_str.startswith("It's ")
        assert "," in date_str
