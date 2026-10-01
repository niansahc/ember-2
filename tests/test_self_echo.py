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

    def test_the_prior_provides_no_defence_in_depth_for_an_assistant_answer(self):
        """There is no second layer. The predicate is the whole mechanism.

        This test used to claim the opposite -- "content_kind=answer keeps a
        discount inside the prior (KIND_ANSWER, 0.9752), so an assistant answer
        reaching the ranker through some path the predicate does not cover is
        still disadvantaged" -- and it asserted `score < 0.5` to prove it.

        Both halves were wrong. KIND_ANSWER has been exactly 1.0 since ADR-044's
        magnitudes were derived, and the assertion passed anyway because the
        fixture is 46 characters, so LEN_UNDER_50 was doing the work the
        docstring credited to the kind term. A test can pass for a reason its
        own docstring denies, and that is what makes a stale docstring
        load-bearing rather than cosmetic.

        KIND_ANSWER's retirement is sound and was re-verified on the corrected
        delivery endpoint (#227): ST 0.0000 and solo-flat under both endpoint
        definitions, and a direct probe moves zero delivered refs at either end
        of its range. So the honest statement is that role exclusion has ONE
        owner, role_predicate, and anything reaching the ranker past it is
        treated on its merits like any other record.
        """
        from src.context import prior

        assert prior.KIND_ANSWER == 1.0, (
            "the prior discounts content_kind=answer again; if that is "
            "deliberate, this test and ADR-044's kind.answer rule both need "
            "rewriting, because the predicate was made the sole owner"
        )

        ranker = ContextRanker()
        long_enough = "Here are the patterns I have noticed across your work this month"
        assert len(long_enough) > prior.SHORT_CHARS
        answer = make_item(
            long_enough, score=0.5, role="assistant", content_kind="answer"
        )
        # content_kind=None takes the kind family's named identity arm. NOT
        # make_item's default, which is "user_content" and carries a boost --
        # comparing against that would measure the user_content arm instead.
        no_kind = make_item(long_enough, score=0.5, role="assistant", content_kind=None)

        # Same body, same length, same role: the only difference is the kind,
        # and it buys nothing.
        assert ranker._score_memory_item(answer).score == pytest.approx(
            ranker._score_memory_item(no_kind).score
        )

    def test_user_content_is_favoured_over_a_neutral_record(self):
        """KIND_USER_CONTENT lifts user-authored content.

        The short-content case used to contradict this and no longer does. Under
        the magnitudes derived from #232, LEN_UNDER_50 (0.9339) outweighed
        KIND_USER_CONTENT (1.0248), so a 46-character user turn entering at 0.5
        finalised at 0.4785 -- net penalised for being short and
        user-authored, which is most of a conversational vault. That was #250.

        It is resolved by measurement rather than by a special case: on the
        production corpus kind.user_content outranks len.lt50, under both the
        packet and the corrected rendered endpoint, so the product is above 1.0
        and a short user turn is lifted rather than penalised. The ordering, not
        the allocation, was what decided the sign -- see ADR-044's 2026-09-30
        and 2026-10-01 amendments.

        Both the over-50 and under-50 cases are asserted below, because the
        under-50 case is the one that was broken and a test that only covered
        the safe length would not notice it regressing.
        """
        from src.context import prior

        ranker = ContextRanker()

        long_body = "I've been focused on the state layer this week and it is going well"
        assert len(long_body) > prior.SHORT_CHARS
        assert ranker._score_memory_item(
            make_item(long_body, score=0.5, role="user", content_kind="user_content")
        ).score > 0.5

        # #250: the case that used to come out net penalised.
        short_body = "the state layer landed today"
        assert len(short_body) < prior.SHORT_CHARS
        assert ranker._score_memory_item(
            make_item(short_body, score=0.5, role="user", content_kind="user_content")
        ).score > 0.5, (
            "a short user-authored record is net penalised again; #250 has "
            "regressed, which means kind.user_content stopped outranking "
            "len.lt50 in the run the magnitudes were derived from"
        )


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
