"""
tests/test_reflection_retrieval_scoring.py

How ContextRetriever.get_reflection_items() scores reflection candidates.

Two corrections deep. Recalling Too Well Phase 1, items 4-5, replaced a
hardcoded score of 1.0 -- above anything a cosine-derived memory item
could reach -- with a token Jaccard score against the query, and removed
the unconditional "most recent reflection" fallback.

Issue #239 found the replacement no better calibrated than the thing it
replaced, in the opposite direction. Jaccard is a number between 0 and 1
and the gate that judges it (_apply_type_gate, min_score 0.25) is
calibrated for cosine, so the channel produced 100 candidates over 36
queries, the best scored 0.0794, and the gate rejected every one. A
channel with a weight on every policy delivered nothing.

Reflections are now scored by cosine through the same semantic_search
path as the memory channel, so the gate compares like with like.

Fixtures are synthetic (CLAUDE.md Vault Privacy Rule).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.context.models import ContextItem
from src.context.retriever import ContextRetriever

QUERY = "what patterns have shown up in how i work"


SYNTHESIS = (
    "a weekly synthesis noting that focus recurred around indexing work "
    "and that the pattern held across several sessions"
)


def _result(record_id: str, score: float, content: str = SYNTHESIS):
    """One semantic_search hit, as the reflection path consumes it."""
    return {
        "id": record_id,
        "content": content,
        "score": score,
        "raw_score": score,
        "created_at": "2026-09-01T00-00-00",
        "tier": "cold",
        "authorship": "unknown",
        "metadata": {"tags": ["weekly"]},
    }


def _make_retriever() -> ContextRetriever:
    memory_service = MagicMock()
    # Either call would be a regression: search() is the keyword path this
    # channel no longer uses, read() is the removed unconditional fallback.
    memory_service.search.side_effect = AssertionError(
        "the reflection channel must not use MemoryService.search() -- it "
        "returns no score, which is what started this (#239)"
    )
    memory_service.read.side_effect = AssertionError(
        "get_reflection_items must not call memory_service.read() -- the "
        "unconditional fallback was removed (item 5)"
    )
    return ContextRetriever(memory_service=memory_service)


class TestScoredByCosine:
    def test_the_score_comes_from_semantic_search(self):
        retriever = _make_retriever()
        with patch("src.retrieval.semantic_search.semantic_search",
                   return_value=[_result("r1", 0.6207)]):
            items = retriever.get_reflection_items(QUERY)

        assert len(items) == 1
        assert items[0].score == pytest.approx(0.6207)

    def test_it_searches_the_reflection_type_and_reuses_the_embedding(self):
        """A second embed call per turn is the cost this channel used to
        avoid by not embedding at all."""
        retriever = _make_retriever()
        embedding = [0.1] * 768
        with patch("src.retrieval.semantic_search.semantic_search",
                   return_value=[]) as search:
            retriever.get_reflection_items(QUERY, query_embedding=embedding)

        _args, kwargs = search.call_args
        assert kwargs["memory_type"] == "reflection"
        assert kwargs["query_embedding"] is embedding

    def test_a_better_match_scores_higher(self):
        retriever = _make_retriever()
        with patch("src.retrieval.semantic_search.semantic_search",
                   return_value=[_result("r1", 0.84), _result("r2", 0.52)]):
            items = retriever.get_reflection_items(QUERY)

        assert items[0].score > items[1].score

    def test_the_score_is_on_the_scale_the_gate_expects(self):
        """The defect, as a test.

        0.25 is the gate. A Jaccard score between a short query and a
        multi-paragraph synthesis sits an order of magnitude below it, so
        the channel could not deliver whatever the corpus contained. A
        cosine score from the same path the memory channel uses can.
        """
        from src.context.policies import ContextPolicy
        from src.context.service import ContextService

        retriever = _make_retriever()
        with patch("src.retrieval.semantic_search.semantic_search",
                   return_value=[_result("r1", 0.6207)]):
            items = retriever.get_reflection_items(QUERY)

        kept = ContextService()._apply_type_gate(items, ContextPolicy(name="default"))
        assert kept, "a well-matched reflection must survive the min_score gate"

    def test_store_id_is_the_record_id(self):
        """ADR-015 step 4: retrieval stats key on store_id."""
        retriever = _make_retriever()
        with patch("src.retrieval.semantic_search.semantic_search",
                   return_value=[_result("r1", 0.7)]):
            items = retriever.get_reflection_items(QUERY)
        assert items[0].store_id == "r1"

    def test_raw_score_is_carried_for_downstream_consumers(self):
        retriever = _make_retriever()
        with patch("src.retrieval.semantic_search.semantic_search",
                   return_value=[_result("r1", 0.7)]):
            items = retriever.get_reflection_items(QUERY)
        assert items[0].metadata["raw_score"] == pytest.approx(0.7)

    def test_empty_search_yields_empty_items(self):
        """No unconditional most-recent-reflection fallback (item 5)."""
        retriever = _make_retriever()
        with patch("src.retrieval.semantic_search.semantic_search",
                   return_value=[]):
            assert retriever.get_reflection_items(QUERY) == []


class TestCrossChannelDuplication:
    """A reflection that clears the gate can arrive on both channels.

    The memory channel searches every migrated type, reflection included,
    and dedup runs per channel. While the reflection channel delivered
    nothing this was unreachable; scoring it by cosine makes it live.
    Measured on the production corpus: 6 records across 3 of 36 turns.
    """

    def _item(self, store_id, content, memory_type="reflection"):
        return ContextItem(
            id=store_id,
            content=content,
            source=memory_type,
            item_type=memory_type,
            memory_type=memory_type,
            store_id=store_id,
        )

    def test_a_record_in_both_channels_is_dropped_from_memory(self):
        retriever = _make_retriever()
        shared = self._item("r1", SYNTHESIS)
        memory = [self._item("c1", "a conversation turn", "conversation"), shared]
        reflections = [self._item("r1", SYNTHESIS)]

        kept = retriever._drop_reflection_duplicates(memory, reflections)

        assert [i.store_id for i in kept] == ["c1"]

    def test_the_reflection_copy_is_the_one_retained(self):
        """It carries the derived-synthesis framing and the age label."""
        retriever = _make_retriever()
        reflections = [self._item("r1", SYNTHESIS)]
        kept = retriever._drop_reflection_duplicates(
            [self._item("r1", SYNTHESIS)], reflections
        )
        assert kept == []
        assert len(reflections) == 1

    def test_matching_content_with_a_different_id_is_also_dropped(self):
        """The prior-substrate corpus carries ids minted elsewhere, so id
        equality alone would miss a genuine duplicate."""
        retriever = _make_retriever()
        kept = retriever._drop_reflection_duplicates(
            [self._item("other-id", "  " + SYNTHESIS.upper() + "  ")],
            [self._item("r1", SYNTHESIS)],
        )
        assert kept == []

    def test_unrelated_memory_items_are_untouched(self):
        retriever = _make_retriever()
        memory = [self._item(f"c{n}", f"turn number {n}", "conversation")
                  for n in range(3)]
        kept = retriever._drop_reflection_duplicates(
            memory, [self._item("r1", SYNTHESIS)]
        )
        assert kept == memory

    def test_no_reflections_means_no_filtering(self):
        retriever = _make_retriever()
        memory = [self._item("c1", "a conversation turn", "conversation")]
        assert retriever._drop_reflection_duplicates(memory, []) == memory
