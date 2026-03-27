"""
tests/test_self_echo.py

Tests for assistant self-echo prevention.
Ensures assistant conversation turns are penalized in scoring and
labeled correctly in the prompt so the model doesn't attribute
Ember's own words back to the user.
"""

import pytest

from src.context.ranker import ContextRanker
from src.context.models import ContextItem


def make_item(content="test", score=0.5, role="user", content_kind="user_content"):
    return ContextItem(
        id="test",
        content=content,
        source="conversation",
        item_type="conversation",
        score=score,
        metadata={"role": role, "content_kind": content_kind},
    )


class TestRankerAssistantPenalty:
    """Assistant turns should score significantly lower than user turns."""

    def test_assistant_gets_strong_penalty(self):
        ranker = ContextRanker()
        user_item = make_item("I'm working on the retrieval pipeline", score=0.5, role="user")
        asst_item = make_item("I can help with the retrieval pipeline", score=0.5, role="assistant", content_kind="answer")

        ranked, _ = ranker.rank([user_item, asst_item], [])

        user_score = next(i.score for i in ranked if i.metadata["role"] == "user")
        asst_score = next(i.score for i in ranked if i.metadata["role"] == "assistant")

        # Assistant should be at least 0.30 below user (role penalty + content_kind penalty)
        assert user_score - asst_score > 0.30

    def test_assistant_answer_gets_double_penalty(self):
        ranker = ContextRanker()
        item = make_item(
            "Here are the patterns I've noticed in your work",
            score=0.5, role="assistant", content_kind="answer",
        )
        scored = ranker._score_memory_item(item)
        # role=assistant: -0.25, content_kind=answer: -0.10 = -0.35 total
        # conversation type: +0.10, so net should be well below starting score
        assert scored.score < 0.5

    def test_user_gets_positive_boost(self):
        ranker = ContextRanker()
        item = make_item(
            "I've been focused on the state layer this week",
            score=0.5, role="user", content_kind="user_content",
        )
        scored = ranker._score_memory_item(item)
        # role=user: +0.12, content_kind=user_content: +0.05, conversation: +0.10
        assert scored.score > 0.5


class TestSourceQualityAdjustment:
    """source_quality_adjustment should use metadata.role when available."""

    def test_metadata_role_assistant_penalty(self):
        from src.retrieval.semantic_search import source_quality_adjustment
        score = source_quality_adjustment("some content", {"role": "assistant"})
        assert score < -0.15  # -0.20 for role, plus other adjustments

    def test_metadata_role_user_bonus(self):
        from src.retrieval.semantic_search import source_quality_adjustment
        score = source_quality_adjustment("I am working on something important today", {"role": "user"})
        assert score > 0.10  # +0.16 for role, plus experience markers

    def test_no_metadata_falls_back_to_prefix(self):
        from src.retrieval.semantic_search import source_quality_adjustment
        score_user = source_quality_adjustment("user: hello world this is a test message", None)
        score_asst = source_quality_adjustment("assistant: hello world this is a test", None)
        assert score_user > score_asst

    def test_metadata_overrides_prefix(self):
        from src.retrieval.semantic_search import source_quality_adjustment
        # Content starts with "user:" but metadata says assistant — metadata wins
        score = source_quality_adjustment("user: this looks like a user turn", {"role": "assistant"})
        assert score < 0  # assistant penalty applied


class TestPromptBuilderRoleLabels:
    """Context section should label user vs assistant turns."""

    def test_user_turn_labeled(self):
        from src.llm.prompt_builder import PromptBuilder
        from src.context.models import ContextPacket

        packet = ContextPacket(
            user_message="test",
            memory_items=[
                ContextItem(
                    id="1", content="I need help with X", source="conversation",
                    item_type="conversation", score=0.5,
                    metadata={"role": "user"},
                ),
            ],
        )
        builder = PromptBuilder()
        prompt = builder._build_context_section(packet)
        assert "[you said]" in prompt
        assert "[Ember said]" not in prompt

    def test_assistant_turn_labeled(self):
        from src.llm.prompt_builder import PromptBuilder
        from src.context.models import ContextPacket

        packet = ContextPacket(
            user_message="test",
            memory_items=[
                ContextItem(
                    id="1", content="Here's what I found", source="conversation",
                    item_type="conversation", score=0.5,
                    metadata={"role": "assistant"},
                ),
            ],
        )
        builder = PromptBuilder()
        prompt = builder._build_context_section(packet)
        assert "[Ember said]" in prompt
        assert "[you said]" not in prompt

    def test_non_conversation_unchanged(self):
        from src.llm.prompt_builder import PromptBuilder
        from src.context.models import ContextPacket

        packet = ContextPacket(
            user_message="test",
            memory_items=[
                ContextItem(
                    id="1", content="Some ingested content", source="ingested",
                    item_type="ingested", score=0.5, metadata={},
                ),
            ],
        )
        builder = PromptBuilder()
        prompt = builder._build_context_section(packet)
        assert "(ingested)" in prompt
        assert "[you said]" not in prompt
        assert "[Ember said]" not in prompt

    def test_mixed_roles_both_labeled(self):
        from src.llm.prompt_builder import PromptBuilder
        from src.context.models import ContextPacket

        packet = ContextPacket(
            user_message="test",
            memory_items=[
                ContextItem(
                    id="1", content="User content here", source="conversation",
                    item_type="conversation", score=0.5,
                    metadata={"role": "user"},
                ),
                ContextItem(
                    id="2", content="Ember content here", source="conversation",
                    item_type="conversation", score=0.4,
                    metadata={"role": "assistant"},
                ),
            ],
        )
        builder = PromptBuilder()
        prompt = builder._build_context_section(packet)
        assert "[you said]" in prompt
        assert "[Ember said]" in prompt
