"""
tests/test_min_score_sqlite_floor.py

Regression guard for the dead min_score floor on the SQLite path (#205).

Before this fix, semantic_search forwarded min_score only to
VectorIndex.search -- the JSON fallback -- and every live memory type is
SQLite-backed, so the parameter never filtered anything a user could reach.
get_profile_items asked for a 0.3 floor and got none, which is why profile
guaranteed slots fired on every query once the index was rebuilt (#211).

Two properties are pinned here, and the second is the one that matters:

  1. The floor applies on the SQLite path at all.
  2. It gates RAW COSINE, not the adjusted score. The adjusted score carries
     a query-independent constant pile large enough to lift a record over a
     floor that exists to keep it out, so a floor on the adjusted score would
     be a floor in name only. This is the same distinction ADR-044 draws.

The default is None rather than the historical 0.20. That default was inert,
and enforcing it on the SQLite path would have applied a floor to all of
get_memory_items' retrieval -- a much larger change than repairing the floor.
The test for that is below, because a future edit restoring the numeric
default would silently filter live memory retrieval.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import src.retrieval.semantic_search as ss

EMBED_DIM = 8


def _store_row(record_id: str, text: str, score: float, memory_type: str = "profile"):
    """A row in the shape SqliteVectorStore.search returns."""
    return {
        "id": record_id,
        "content": text,
        "score": score,
        "memory_type": memory_type,
        "metadata": {"role": "user"},
        "timestamp": "2026-09-01T12-00-00",
        "tags": [],
    }


class _FakeStore:
    def __init__(self, rows):
        self._rows = rows

    def search(self, query_embedding, limit, memory_type=None):
        return [dict(r) for r in self._rows if memory_type in (None, r["memory_type"])]


@pytest.fixture
def patched_search(tmp_path, monkeypatch):
    """semantic_search against a fake memory.db, with no ingested store."""
    (tmp_path / "embeddings").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ss, "get_private_vault_path", lambda: tmp_path)
    monkeypatch.setattr(ss, "embed_text", lambda _q: [0.1] * EMBED_DIM)
    monkeypatch.setattr(ss, "_get_sqlite_store", lambda: None)

    def _install(rows):
        monkeypatch.setattr(ss, "_get_memory_store", lambda: _FakeStore(rows))

    return _install


# A body long enough to clear should_exclude_result's 40-char floor.
_LONG = "a profile statement with enough length to clear the short content floor"


def test_floor_applies_on_the_sqlite_path(patched_search):
    patched_search([
        _store_row("above", _LONG + " one", 0.60),
        _store_row("below", _LONG + " two", 0.10),
    ])
    ids = {r["id"] for r in ss.semantic_search("q", memory_type="profile", min_score=0.30)}
    assert ids == {"above"}


def test_floor_gates_raw_cosine_not_the_adjusted_score(patched_search):
    """The load-bearing property.

    This row's raw cosine is below the floor, but the constant pile applied
    after it (type, role, content and lexical terms) lifts the adjusted score
    well above. If the floor were applied to the adjusted score the record
    would survive, and the floor would be decorative.
    """
    patched_search([_store_row("low_cosine", "user: " + _LONG, 0.12)])

    kept = ss.semantic_search("q", memory_type="profile", min_score=0.30)
    assert kept == []

    unfloored = ss.semantic_search("q", memory_type="profile", min_score=None)
    assert len(unfloored) == 1
    assert unfloored[0]["raw_score"] == pytest.approx(0.12)
    assert unfloored[0]["score"] > 0.30, (
        "fixture no longer demonstrates the property: the adjusted score must "
        "clear the floor that the raw cosine does not"
    )


def test_none_means_no_floor(patched_search):
    patched_search([
        _store_row("a", _LONG + " one", 0.60),
        _store_row("b", _LONG + " two", 0.01),
    ])
    ids = {r["id"] for r in ss.semantic_search("q", memory_type="profile", min_score=None)}
    assert ids == {"a", "b"}


def test_default_is_none_so_untouched_callers_are_unfiltered(patched_search):
    """get_memory_items does not pass min_score.

    Restoring a numeric default would apply a floor to all memory retrieval
    without any caller asking for one. That is the change this fix
    deliberately did not make.
    """
    import inspect

    assert inspect.signature(ss.semantic_search).parameters["min_score"].default is None

    patched_search([_store_row("weak", _LONG, 0.05, memory_type="conversation")])
    assert len(ss.semantic_search("q", memory_type="conversation")) == 1


def test_floor_applies_to_the_all_types_branch(patched_search):
    patched_search([
        _store_row("keep", _LONG + " one", 0.60, memory_type="conversation"),
        _store_row("drop", _LONG + " two", 0.10, memory_type="journal"),
    ])
    ids = {r["id"] for r in ss.semantic_search("q", memory_type=None, min_score=0.30)}
    assert ids == {"keep"}


def test_boundary_is_inclusive(patched_search):
    patched_search([_store_row("exact", _LONG, 0.30)])
    assert len(ss.semantic_search("q", memory_type="profile", min_score=0.30)) == 1


# ---------------------------------------------------------------------------
# What the caller asks for
# ---------------------------------------------------------------------------

def test_profile_asks_for_a_floor_and_identity_queries_waive_it():
    """get_profile_items' two modes, pinned at the call site.

    The waiver matters: an identity query is exactly the case where a profile
    record should surface regardless of cosine, and a future change tightening
    the floor must not tighten it here.
    """
    from src.context.retriever import ContextRetriever

    retriever = ContextRetriever()
    seen = {}

    def _capture(user_message, memory_type=None, limit=None, min_score=None, query_embedding=None):
        seen["min_score"] = min_score
        seen["limit"] = limit
        return []

    with patch("src.context.retriever._semantic_search", side_effect=_capture):
        retriever.get_profile_items("what is the weather like")
        assert seen["min_score"] == 0.3

        retriever.get_profile_items("who am i")
        assert seen["min_score"] == 0.0
