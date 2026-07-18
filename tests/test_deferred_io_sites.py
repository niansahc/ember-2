"""
tests/test_deferred_io_sites.py

Regression tests for B-IO-001 (ADR-039 follow-up): the three JSON I/O sites
deferred from A3 must route through the safe helpers so a torn write can no
longer corrupt or destroy a prior file, and a corrupt file is skipped rather
than crashing the read path.

Deferred sites covered:
  1. src/retrieval/vector_index.py  -- save_index / load_index (derived index)
  2. src/ingest/writers.py          -- per-chunk write path
  3. src/state/state_service.py     -- write() atomicity

Each "torn write" is simulated by handing the writer a payload containing a
value json.dump cannot serialise (a set), which raises mid-serialisation --
the same on-disk outcome as a crash or power loss part-way through a write.
With a bare open("w") + json.dump the target is truncated the instant it is
opened, so the prior file is lost; with the atomic temp+replace helper the
prior file survives untouched.

All tests use tmp_path; no real vault is touched.
"""

import json

import pytest

from src.core.jsonio import safe_read_json


# ---------------------------------------------------------------------------
# Site 1: retrieval/vector_index.py
# ---------------------------------------------------------------------------

class TestVectorIndexWriteAtomicity:
    def test_failed_save_preserves_prior_index(self, tmp_path):
        """A save that fails mid-serialisation must leave the previously
        written index intact and loadable, not a truncated/empty file."""
        from src.retrieval.vector_index import VectorIndex, clear_index_cache

        clear_index_cache()
        vi = VectorIndex()
        path = tmp_path / "ingested_index.json"

        good = [{"file_path": "/a.json", "embedding": [0.1, 0.2], "text": "hello"}]
        vi.save_index(path, good)

        # A set is not JSON-serialisable: json.dump raises part-way through,
        # simulating an interrupted (torn) write.
        torn = [{"file_path": "/b.json", "embedding": {1, 2, 3}, "text": "boom"}]
        with pytest.raises(Exception):
            vi.save_index(path, torn)

        # Read fresh from disk (bypass the in-memory cache).
        clear_index_cache()
        assert vi.load_index(path) == good

    def test_corrupt_index_read_skips_without_crash(self, tmp_path):
        """A corrupt index file is skipped (empty result), never raised."""
        from src.retrieval.vector_index import VectorIndex, clear_index_cache

        clear_index_cache()
        vi = VectorIndex()
        path = tmp_path / "conversation_index.json"
        path.write_text("{not valid json", encoding="utf-8")

        assert vi.load_index(path) == []


# ---------------------------------------------------------------------------
# Site 2: ingest/writers.py
# ---------------------------------------------------------------------------

class TestIngestWriterAtomicity:
    def _chunk(self, content, metadata):
        from src.ingest.models import ChunkedDocument

        return ChunkedDocument(
            source="test",
            doc_id="doc1",
            chunk_id="doc1_chunk_0",
            title="t",
            created_at="2026-07-18T10-00-00",
            content=content,
            metadata=metadata,
        )

    def test_failed_chunk_write_preserves_prior_chunk(self, tmp_path):
        """A torn chunk write must not destroy the chunk file already on
        disk for that chunk_id."""
        from src.ingest.writers import write_chunks_to_vault

        # index_for_retrieval=False keeps the valid write off the embed path,
        # so the test needs no running embedding model.
        good = self._chunk("valid content", {"index_for_retrieval": False})
        write_chunks_to_vault([good], tmp_path)

        chunk_file = tmp_path / "memory" / "ingested" / "doc1_chunk_0.json"
        assert safe_read_json(chunk_file, default=None) is not None

        # A set in metadata makes json.dump raise before the index step runs.
        torn = self._chunk("boom", {"bad": {1, 2, 3}})
        with pytest.raises(Exception):
            write_chunks_to_vault([torn], tmp_path)

        reloaded = safe_read_json(chunk_file, default=None)
        assert reloaded is not None
        assert reloaded["content"] == "valid content"


# ---------------------------------------------------------------------------
# Site 3: state/state_service.py write()
# ---------------------------------------------------------------------------

class TestStateWriteAtomicity:
    def test_torn_write_leaves_no_corrupt_record(self, tmp_path):
        """A write that fails mid-serialisation must not leave a truncated
        record file in the state directory; read_all stays healthy."""
        from src.state.models import StateRecord
        from src.state.state_service import StateService

        service = StateService(vault_path=tmp_path)
        record = StateRecord(
            id="2026-07-18T10-00-00",
            timestamp="2026-07-18T10-00-00",
            type="current_focus",
            text="x",
            source="test",
            metadata={"bad": {1, 2, 3}},
        )

        with pytest.raises(Exception):
            service.write(record)

        file_path = tmp_path / "memory" / "state" / service._filename_for(record)
        assert not file_path.exists()
        # No corrupt file lingering means read_all recovers cleanly.
        assert service.read_all() == []
