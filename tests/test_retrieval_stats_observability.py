"""
tests/test_retrieval_stats_observability.py

The retrieval-stat write is best-effort by design. These tests pin that it is
no longer SILENT, which is a different property and the one that cost two
measurements.

Why this file exists. ADR-015's activation model has exactly one upward path:
a delivered record gets `last_retrieved_at` and `frequency_score`, and heat
follows. Two separate failures in that path produced no signal at all.

The first was a key mismatch. `ContextItem.id` was passed where `vectors.id`
was wanted, so every SELECT in `update_retrieval_stats` returned None and every
record was skipped. Not a zero-rowcount UPDATE, which at least has a rowcount
to look at, but no statement issued. `frequency_score` never moved for anything
but reflections and nothing reported it.

The second was the diagnosis of the first. Issue #252 measured
`last_retrieved_at` populated on 0 of 18,681 rows and named "retrieval stats are
not being written" as its leading hypothesis. The zeros were equally consistent
with a live defect and with a working mechanism whose history had been cleared
on purpose by `tools/rebuild_tiers.py --reset-delivery-signal`. Telling those
apart took a comparison against two database backups. A matched count at the
write site answers it in one line.

So: three outcomes, not two, and each one distinguishable. Nothing requested.
Requested and matched. Requested and missed. The read-only suppression is a
fourth and is explicitly not a mismatch.

Every assertion here has its negative twin in the same file, per CLAUDE.md's
rule that an absence assertion ships with a positive control: a test that the
warning appears is satisfied by a fixture where the write could never have
succeeded, so each one is paired with the clean case asserting silence.

No guard counters. `build_context` opens the counter scope and returns, while
this write fires later from `ContextPacket.commit_delivery` in the adapter, so a
`count()` at the write site records nothing in production either. The log is the
channel, and that is what these tests read.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.retrieval.sqlite_vector_store import RetrievalStatsWrite, SqliteVectorStore

EMBEDDING = [0.1] * 768


def _store(tmp_path: Path, ids: tuple[str, ...]) -> SqliteVectorStore:
    store = SqliteVectorStore(tmp_path / "memory.db")
    for record_id in ids:
        store.insert({
            "id": record_id,
            "text": "a synthetic record with enough body to clear the floors",
            "embedding": EMBEDDING,
            "source": "test",
            "memory_type": "conversation",
            "created_at": "2026-04-03",
            "metadata": {},
        })
    return store


# ---------------------------------------------------------------------------
# The result type. Three outcomes, and the fourth that must not read as one.
# ---------------------------------------------------------------------------

def test_nothing_requested_is_not_a_mismatch():
    result = RetrievalStatsWrite(requested=0, matched=0)
    assert result.missed == 0
    assert not result.all_missed


def test_every_id_missing_is_the_mismatch_signature():
    result = RetrievalStatsWrite(requested=4, matched=0)
    assert result.missed == 4
    assert result.all_missed


def test_some_ids_missing_is_not_the_mismatch_signature():
    """One stale id is a corpus fact. All of them cannot be."""
    result = RetrievalStatsWrite(requested=4, matched=3)
    assert result.missed == 1
    assert not result.all_missed


def test_read_only_suppression_is_not_a_mismatch():
    """The fourth outcome, and the reason `suppressed` is a field.

    A replay harness asks for records and writes none deliberately. Folding
    that into matched=0 would hand every harness run a false key-mismatch
    alarm, which is how a detector stops being read.
    """
    result = RetrievalStatsWrite(requested=6, matched=0, suppressed=True)
    assert result.missed == 0
    assert not result.all_missed


# ---------------------------------------------------------------------------
# The store-level detector, each case against its twin.
# ---------------------------------------------------------------------------

def test_a_key_mismatch_is_reported_and_named(tmp_path, caplog):
    """The regression test for the ADR-015 defect, at the site it lived.

    Ids that are not in the table at all, which is what passing `.id` where
    `store_id` was wanted produced for every non-reflection record.
    """
    store = _store(tmp_path, ("real-1", "real-2"))
    with caplog.at_level(logging.WARNING):
        result = store.update_retrieval_stats(
            ["/a/file/path.json", "/another/file/path.json"]
        )
    store.close()

    assert result.requested == 2
    assert result.matched == 0
    assert result.all_missed

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "matched 0 of 2" in messages
    assert "key mismatch" in messages
    assert "store_id" in messages, (
        "the warning must name the field the caller should have supplied, or it "
        "reports a symptom and leaves the reader to rediscover the cause"
    )


def test_a_clean_write_is_silent(tmp_path, caplog):
    """The twin. Without this, the test above passes on a logger that warns
    unconditionally, and a detector that always fires is not a detector.
    """
    store = _store(tmp_path, ("real-1", "real-2"))
    with caplog.at_level(logging.WARNING):
        result = store.update_retrieval_stats(["real-1", "real-2"])
    store.close()

    assert result.matched == 2
    assert result.missed == 0
    assert not result.all_missed
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_a_partial_miss_is_reported_as_a_partial_miss(tmp_path, caplog):
    """And not as a mismatch, because the two have different causes."""
    store = _store(tmp_path, ("real-1",))
    with caplog.at_level(logging.WARNING):
        result = store.update_retrieval_stats(["real-1", "gone-2"])
    store.close()

    assert (result.matched, result.missed) == (1, 1)
    assert not result.all_missed

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "matched 1 of 2" in messages
    assert "key mismatch" not in messages


def test_an_empty_request_writes_nothing_and_says_nothing(tmp_path, caplog):
    store = _store(tmp_path, ("real-1",))
    with caplog.at_level(logging.WARNING):
        result = store.update_retrieval_stats([])
    store.close()

    assert (result.requested, result.matched) == (0, 0)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_the_detector_logs_no_record_id(tmp_path, caplog):
    """Counts only. A record id is vault content and logs/ is in the repo tree.

    The ids here are distinctive on purpose so a leak is detectable.
    """
    store = _store(tmp_path, ("real-1",))
    with caplog.at_level(logging.WARNING):
        store.update_retrieval_stats(["ZZLEAKZZ-one", "ZZLEAKZZ-two"])
    store.close()

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert messages, "the detector did not fire, so this test proves nothing"
    assert "ZZLEAKZZ" not in messages


# ---------------------------------------------------------------------------
# The service-level handler.
# ---------------------------------------------------------------------------

def _service():
    from src.context.service import ContextService

    return ContextService.__new__(ContextService)


def _delivered(store_id: str | None, memory_type: str = "conversation"):
    from src.context.models import ContextItem

    return ContextItem(
        id="/a/file/path.json",
        store_id=store_id,
        content="a synthetic record with enough body to clear the floors",
        source="chat",
        item_type=memory_type,
        memory_type=memory_type,
        score=0.5,
        metadata={},
    )


def test_a_raising_store_is_logged_rather_than_swallowed(caplog, monkeypatch):
    """The bare `except Exception: pass` regression.

    A store that raises on every turn used to be indistinguishable from one
    that wrote cleanly. Both the swallow and the report are asserted: the call
    must not raise, and it must not be quiet.
    """
    service = _service()
    service.debug = False

    class _Raises:
        def update_retrieval_stats(self, ids):
            raise RuntimeError("store exploded with /a/file/path.json in the text")

    monkeypatch.setattr(
        "src.retrieval.semantic_search._get_memory_store", lambda: _Raises()
    )
    monkeypatch.setattr(
        "src.retrieval.semantic_search._get_sqlite_store", lambda: None
    )

    with caplog.at_level(logging.WARNING):
        service._update_retrieval_stats([_delivered("real-1")])  # must not raise

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "retrieval-stat write failed" in messages
    assert "RuntimeError" in messages, (
        "the exception type is what makes the log actionable"
    )
    assert "/a/file/path.json" not in messages, (
        "the exception MESSAGE must not be logged: it can carry a record id, "
        "which is vault content"
    )


def test_a_working_store_logs_no_failure(caplog, monkeypatch):
    """The twin for the handler."""
    service = _service()
    service.debug = False
    calls = []

    class _Works:
        def update_retrieval_stats(self, ids):
            calls.append(list(ids))
            return RetrievalStatsWrite(requested=len(ids), matched=len(ids))

    monkeypatch.setattr(
        "src.retrieval.semantic_search._get_memory_store", lambda: _Works()
    )
    monkeypatch.setattr(
        "src.retrieval.semantic_search._get_sqlite_store", lambda: None
    )

    with caplog.at_level(logging.WARNING):
        service._update_retrieval_stats([_delivered("real-1")])

    assert calls == [["real-1"]], "the store was not reached"
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_delivered_records_with_no_store_id_are_reported(caplog, monkeypatch):
    """The third silent case: armed, fired, and nothing routable.

    This is the ADR-015 defect one level up from the SELECT. If every delivered
    record lost its store_id, both id lists are empty, both stores are skipped,
    and the write reports success by doing nothing at all.
    """
    service = _service()
    service.debug = False
    monkeypatch.setattr(
        "src.retrieval.semantic_search._get_memory_store", lambda: None
    )
    monkeypatch.setattr(
        "src.retrieval.semantic_search._get_sqlite_store", lambda: None
    )

    with caplog.at_level(logging.WARNING):
        service._update_retrieval_stats([_delivered(None), _delivered(None)])

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "routed 0 of 2" in messages
    assert "store_id" in messages


def test_an_unroutable_memory_type_is_reported(caplog, monkeypatch):
    """Same detector, other cause: a store_id that belongs to no store.

    `state` and `task` records reach the packet but no vector store owns them,
    so they route nowhere. Today that is correct and silent; the point is that
    it is now visible, because the same silence would hide a type the router
    forgot.
    """
    service = _service()
    service.debug = False
    monkeypatch.setattr(
        "src.retrieval.semantic_search._get_memory_store", lambda: None
    )
    monkeypatch.setattr(
        "src.retrieval.semantic_search._get_sqlite_store", lambda: None
    )

    with caplog.at_level(logging.WARNING):
        service._update_retrieval_stats([_delivered("real-1", memory_type="state")])

    assert "routed 0 of 1" in " ".join(r.getMessage() for r in caplog.records)


def test_a_routable_record_does_not_trip_the_routing_detector(caplog, monkeypatch):
    """The twin for the routing detector."""
    service = _service()
    service.debug = False

    class _Works:
        def update_retrieval_stats(self, ids):
            return RetrievalStatsWrite(requested=len(ids), matched=len(ids))

    monkeypatch.setattr(
        "src.retrieval.semantic_search._get_memory_store", lambda: _Works()
    )
    monkeypatch.setattr(
        "src.retrieval.semantic_search._get_sqlite_store", lambda: _Works()
    )

    with caplog.at_level(logging.WARNING):
        service._update_retrieval_stats([
            _delivered("real-1"),
            _delivered("chunk-1", memory_type="ingested"),
        ])

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
