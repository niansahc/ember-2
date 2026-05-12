"""
tests/test_b_ret_002_timestamp_propagation.py

Regression tests for B-RET-002: the data-flow path that carries the
created_at timestamp from the SQLite vectors table into ContextItem.timestamp.

Pre-fix behavior:
  - SqliteVectorStore.search() returned dicts without a created_at key.
  - The JSON metadata blob written by write_memory.py did not include
    created_at either.
  - retriever.get_memory_items() did metadata.get("created_at") which
    returned None for every SQLite-backed record.
  - ContextItem.timestamp was None, _parse_timestamp(None) returned None,
    _format_item_age returned the empty string, and the model saw no
    per-item age labels in the prompt.

Post-fix:
  - sqlite_vector_store.search() includes "created_at": row["created_at"]
    in every result dict.
  - retriever.get_memory_items() reads result.get("created_at") and
    assigns it to ContextItem.timestamp.
  - Per-item age labels can render.
"""

from __future__ import annotations

from unittest.mock import patch

from src.context.retriever import ContextRetriever


def _mock_result(timestamp: str | None) -> list[dict]:
    """Build a single-item search result that matches the shape returned by
    SqliteVectorStore.search() after the B-RET-002 fix."""
    return [
        {
            "content": "Synthetic content for B-RET-002 wiring check.",
            "score": 0.42,
            "path": None,
            "memory_type": "conversation",
            "metadata": {"role": "user", "tags": []},
            "tier": "hot",
            "authorship": "first_person",
            "created_at": timestamp,
            "embedding": [0.0] * 768,
        }
    ]


def test_get_memory_items_propagates_created_at_when_present() -> None:
    """The retriever must read created_at off the result dict (not the
    JSON metadata blob) and put it on ContextItem.timestamp."""
    retriever = ContextRetriever()
    ts = "2026-05-11T18-09-47"
    with patch(
        "src.retrieval.semantic_search.semantic_search",
        return_value=_mock_result(ts),
    ):
        items = retriever.get_memory_items("any query")

    assert len(items) == 1
    assert items[0].timestamp == ts


def test_get_memory_items_timestamp_is_none_when_created_at_missing() -> None:
    """Legacy / non-SQLite rows may have no created_at. ContextItem.timestamp
    must be None in that case, not an exception, not a default age."""
    retriever = ContextRetriever()
    with patch(
        "src.retrieval.semantic_search.semantic_search",
        return_value=_mock_result(None),
    ):
        items = retriever.get_memory_items("any query")

    assert len(items) == 1
    assert items[0].timestamp is None


def test_get_memory_items_does_not_fall_back_to_metadata_created_at() -> None:
    """Sanity pin: even if a future write_memory change ever adds
    created_at to the JSON metadata blob, the retriever must prefer the
    row-level field. Conversely, a created_at value in metadata alone
    must NOT win over result-level absence."""
    retriever = ContextRetriever()
    result = _mock_result(None)
    # Inject a different created_at into the metadata blob.
    result[0]["metadata"]["created_at"] = "1999-01-01T00-00-00"

    with patch(
        "src.retrieval.semantic_search.semantic_search",
        return_value=result,
    ):
        items = retriever.get_memory_items("any query")

    assert len(items) == 1
    # Row-level absence wins over metadata-level presence.
    assert items[0].timestamp is None
