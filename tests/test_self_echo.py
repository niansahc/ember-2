"""
tests/test_self_echo.py

Tests for assistant self-echo prevention.

Ensures assistant conversation turns are excluded from the candidate set
(ADR-044 4a) and, where they reach the prompt by another path, labeled
correctly, so the model doesn't attribute Ember's own words back to the
user.
"""

import pytest

from src.context import role_predicate
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


class TestAssistantContentIsExcluded:
    """Assistant turns are removed from the candidate set, not outscored.

    This class used to assert a score gap of more than 0.30, produced by the
    -0.25 role and -0.10 content_kind penalties in _score_memory_item. Both
    are gone: ADR-044 amendment 4a moved role out of the scoring budget to a
    predicate, on the measurement that a WHERE clause suppresses the
    self-echo incident completely while the pile cost five and a half times
    the entire observable cosine spread to do it.

    A score gap can no longer express this property at all. The prior is
    bounded to [0.9339, 1.1278], so two records entering at 0.5 can finish at
    most 0.5 * (1.1278 - 0.9339) = 0.097 apart. Asserting "more than 0.30"
    against a mechanism whose ceiling is 0.097 would be asserting that the
    contract is broken. The property is now exclusion, so that is what is
    tested. See tests/test_role_predicate.py for the predicate's own contract.
    """

    def test_assistant_turn_never_becomes_a_candidate(self):
        asst_item = make_item(
            "I can help with the retrieval pipeline",
            score=0.5, role="assistant", content_kind="answer",
        )
        assert role_predicate.excluded_by_role(asst_item) is True

    def test_user_turn_survives_alongside_it(self):
        user_item = make_item("I'm working on the retrieval pipeline", score=0.5, role="user")
        asst_item = make_item(
            "I can help with the retrieval pipeline",
            score=0.5, role="assistant", content_kind="answer",
        )

        kept = role_predicate.apply([user_item, asst_item])

        assert [i.metadata["role"] for i in kept] == ["user"]

    def test_an_assistant_answer_that_did_reach_ranking_is_still_discounted(self):
        """Defence in depth, not the primary mechanism.

        content_kind=answer keeps a discount inside the prior (KIND_ANSWER,
        0.9752), so an assistant answer reaching the ranker through some path
        the predicate does not cover is still disadvantaged. Small by design:
        the predicate is what does this job now.
        """
        ranker = ContextRanker()
        item = make_item(
            "Here are the patterns I've noticed in your work",
            score=0.5, role="assistant", content_kind="answer",
        )
        scored = ranker._score_memory_item(item)

        assert scored.score < 0.5

    def test_user_content_is_favoured_over_a_neutral_record(self):
        """KIND_USER_CONTENT (1.0248) lifts user-authored content.

        The fixture is deliberately over 50 characters. Under 50 it is NET
        PENALISED -- LEN_UNDER_50 (0.9339) outweighs KIND_USER_CONTENT, so a
        46-character user turn entering at 0.5 finalizes at 0.4785. That is a
        real consequence of deriving the prior's magnitudes from Sobol ST
        without a sign-interaction check, and most user turns in a
        conversational vault are short. It is recorded in ADR-044's 2026-09-26
        amendment and tracked as a follow-up, NOT worked around here: this
        test states the property the term was meant to have, and the ADR
        states what it actually does.
        """
        ranker = ContextRanker()
        item = make_item(
            "I've been focused on the state layer this week and it is going well",
            score=0.5, role="user", content_kind="user_content",
        )
        scored = ranker._score_memory_item(item)

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
