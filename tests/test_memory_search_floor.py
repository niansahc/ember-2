"""
tests/test_memory_search_floor.py

The raw-cosine floor on the main memory search path.

Phase 2 triage, finding 9. semantic_search declares
`min_score: float | None = None` and the main retrieval path passed
nothing, so `min_score is not None and raw < min_score` was identically
false. The counters said so precisely: 0 firings in 4,248 evaluations on
vector_index.min_score_floor.json and 0 in 2,609 on
semantic_search.min_score_floor.memory_all_types, against 2 of 440 on the
profile path, which does pass one.

That is the hard case for a test to catch. The floor was present,
configured, reachable and running -- it simply could not be true, and no
assertion about behaviour would notice, because a floor that excludes
nothing and a floor that is never armed produce identical output. So the
tests here are about the call, not the outcome.

Fixtures are synthetic (CLAUDE.md Vault Privacy Rule).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.context.retriever import MEMORY_MIN_RAW_SCORE, ContextRetriever

QUERY = "what did i decide about the index rebuild"


def _retriever() -> ContextRetriever:
    return ContextRetriever(memory_service=MagicMock())


class TestTheFloorIsPassed:
    def test_the_main_memory_search_passes_a_floor(self):
        """The defect: this call passed no min_score at all."""
        retriever = _retriever()
        with patch("src.retrieval.semantic_search.semantic_search",
                   return_value=[]) as search:
            retriever.get_memory_items(QUERY, query_embedding=[0.1] * 768)

        _args, kwargs = search.call_args
        assert "min_score" in kwargs, (
            "the main path must pass min_score; omitting it makes the floor "
            "identically false rather than merely inactive"
        )
        assert kwargs["min_score"] == MEMORY_MIN_RAW_SCORE

    def test_the_floor_is_a_number_not_none(self):
        """None restores the defect while looking like configuration."""
        assert MEMORY_MIN_RAW_SCORE is not None
        assert isinstance(MEMORY_MIN_RAW_SCORE, (int, float))

    def test_the_floor_is_in_cosine_range(self):
        """It judges raw cosine, so a value outside 0-1 could never bind."""
        assert 0.0 <= MEMORY_MIN_RAW_SCORE <= 1.0

    def test_the_query_embedding_is_still_reused(self):
        """Adding an argument must not drop the one that saves an embed."""
        retriever = _retriever()
        embedding = [0.2] * 768
        with patch("src.retrieval.semantic_search.semantic_search",
                   return_value=[]) as search:
            retriever.get_memory_items(QUERY, query_embedding=embedding)

        _args, kwargs = search.call_args
        assert kwargs["query_embedding"] is embedding
        assert kwargs["limit"] == 8


class TestTheProfilePathIsUnchanged:
    """The profile search already passed a floor and is not in scope.

    Its values are different on purpose -- 0.0 for an identity query so
    nothing is excluded, 0.3 otherwise -- and this change must not
    homogenise them by accident.
    """

    def test_an_identity_query_still_floors_at_zero(self):
        retriever = _retriever()
        with patch("src.context.retriever._semantic_search",
                   return_value=[]) as search:
            retriever.get_profile_items("who am i", query_embedding=[0.1] * 768)
        assert search.call_args.kwargs["min_score"] == pytest.approx(0.0)

    def test_a_non_identity_query_keeps_its_own_floor(self):
        retriever = _retriever()
        with patch("src.context.retriever._semantic_search",
                   return_value=[]) as search:
            retriever.get_profile_items(QUERY, query_embedding=[0.1] * 768)
        assert search.call_args.kwargs["min_score"] == pytest.approx(0.3)


class TestTheFloorActuallyExcludes:
    """End of the chain: a candidate under the floor does not come back.

    semantic_search owns the comparison, so this is the integration check
    that the value reaches it rather than being accepted and ignored.
    """

    def test_a_result_below_the_floor_is_dropped(self):
        from src.retrieval.sqlite_vector_store import SqliteVectorStore

        below = MEMORY_MIN_RAW_SCORE - 0.05
        above = MEMORY_MIN_RAW_SCORE + 0.05
        store = MagicMock(spec=SqliteVectorStore)
        store.search.return_value = [
            {"id": "low", "content": "a synthetic record that scores poorly "
                                     "against this particular query",
             "score": below, "metadata": {}, "memory_type": "conversation"},
            {"id": "high", "content": "a synthetic record that scores well "
                                      "against this particular query",
             "score": above, "metadata": {}, "memory_type": "conversation"},
        ]

        from src.retrieval import semantic_search as module

        with patch.object(module, "_get_memory_store", return_value=store), \
                patch.object(module, "_get_sqlite_store", return_value=None), \
                patch.object(module, "embed_text", return_value=[0.1] * 768):
            results = module.semantic_search(
                QUERY, limit=8, min_score=MEMORY_MIN_RAW_SCORE,
                query_embedding=[0.1] * 768,
            )

        returned = {r.get("id") for r in results}
        assert "high" in returned
        assert "low" not in returned

    def test_without_a_floor_the_same_result_survives(self):
        """Control: the exclusion above is the floor, not the fixture."""
        from src.retrieval.sqlite_vector_store import SqliteVectorStore

        store = MagicMock(spec=SqliteVectorStore)
        store.search.return_value = [
            {"id": "low", "content": "a synthetic record that scores poorly "
                                     "against this particular query",
             "score": MEMORY_MIN_RAW_SCORE - 0.05, "metadata": {},
             "memory_type": "conversation"},
        ]

        from src.retrieval import semantic_search as module

        with patch.object(module, "_get_memory_store", return_value=store), \
                patch.object(module, "_get_sqlite_store", return_value=None), \
                patch.object(module, "embed_text", return_value=[0.1] * 768):
            results = module.semantic_search(
                QUERY, limit=8, min_score=None, query_embedding=[0.1] * 768,
            )

        assert {r.get("id") for r in results} == {"low"}
