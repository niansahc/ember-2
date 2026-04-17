"""tests/test_ingested_retrieval_scoring.py — task #25 retrieval scoring fixes."""

from __future__ import annotations

import pytest

from src.context.policies import classify_query
from src.retrieval.semantic_search import query_intent_adjustment


class TestStatusStateIncludesIngested:
    """status_state policy should include 'ingested' in eligible types."""

    @pytest.mark.parametrize("query", [
        "what am i working on",
        "what are my priorities",
        "what's my current focus",
    ])
    def test_ingested_in_eligible_types(self, query):
        policy = classify_query(query)
        if policy.eligible_memory_types is not None:
            assert "ingested" in policy.eligible_memory_types


class TestReflectivePenalty:
    """Reflective penalty on ingested should be -0.03, not -0.08."""

    def test_ingested_penalty_reduced(self):
        # Content without a role prefix to isolate the type-level penalty.
        # "user:" prefix adds +0.10 role boost which would mask the result.
        adj = query_intent_adjustment(
            "what patterns have you noticed about me",
            "ingested",
            "i've been working on the garden project all week",
        )
        assert adj == pytest.approx(-0.03, abs=0.001)

    def test_conversation_still_boosted(self):
        adj = query_intent_adjustment(
            "what patterns have you noticed about me",
            "conversation",
            "some conversation content here",
        )
        assert adj >= 0.10

    def test_reflection_still_boosted(self):
        adj = query_intent_adjustment(
            "what patterns have you noticed about me",
            "reflection",
            "weekly reflection content here",
        )
        assert adj >= 0.08


class TestNonReflectiveIngestedUnchanged:
    """Ingested content on non-reflective queries should still get its
    task/work boost when applicable."""

    def test_work_query_ingested_boost(self):
        adj = query_intent_adjustment(
            "how do i fix the authentication pipeline",
            "ingested",
            "i need to debug the auth service error",
        )
        assert adj >= 0.08
