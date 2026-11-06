"""
tests/test_backfill_ingested_index.py

scripts/backfill_ingested_index.py indexes chunk files that the pre-#174
writer left in memory/ingested/ with their embeddings only in the unread
ingested_index.json. The chunk files are the source (rule 4); the JSON
index is a cross-check only.

All content is synthetic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts import backfill_ingested_index as backfill
from src.core.jsonio import safe_write_json
from tests.conftest import _make_vault_tree
from tests.test_ingest_index_routing import fake_embed

DOC = "The harbour master records every vessel that passes the breakwater after dark."
USER = "User: I finally finished sanding the old oak table in the workshop."
ASSISTANT = "Assistant: Oak takes a finish beautifully once it has been sanded."
SUPPRESSED = "User: A synthetic turn that a cleanup pass already suppressed in the index."


def _chunk_file(vault: Path, chunk_id: str, content: str, source: str, metadata: dict) -> Path:
    path = vault / "memory" / "ingested" / f"{chunk_id}.json"
    safe_write_json(path, {
        "type": "ingested", "source": source, "doc_id": chunk_id.rsplit("_chunk_", 1)[0],
        "chunk_id": chunk_id, "title": "t", "created_at": None,
        "content": content, "metadata": metadata,
    })
    return path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """Chunk files as the old writer left them, plus its JSON index."""
    root = tmp_path / "vault"
    _make_vault_tree(root)
    doc = _chunk_file(root, "harbour_log_chunk_0", DOC, "file", {"chunk_index": 0})
    user = _chunk_file(root, "conversations-000_0_chunk_0", USER, "chatgpt",
                       {"role": "user", "index_for_retrieval": True})
    _chunk_file(root, "conversations-000_0_chunk_1", ASSISTANT, "chatgpt",
                {"role": "assistant", "index_for_retrieval": False})
    _chunk_file(root, "conversations-000_1_chunk_0", SUPPRESSED, "chatgpt",
                {"role": "user", "index_for_retrieval": True})
    safe_write_json(root / "embeddings" / "ingested_index.json", [
        {"file_path": str(doc), "embedding": [0.1], "text": DOC, "metadata": {}},
        {"file_path": str(user), "embedding": [0.1], "text": USER, "metadata": {}},
        {"file_path": "/elsewhere/memory/ingested/gone_chunk_0.json",
         "embedding": [0.1], "text": "no canonical file", "metadata": {}},
    ])
    # The suppressed turn is already indexed (migrated id, quality flag).
    from src.retrieval.sqlite_vector_store import SqliteVectorStore

    store = SqliteVectorStore(_memory_db(root))
    store.insert({"id": "migrated-path-id", "text": SUPPRESSED,
                  "embedding": fake_embed(SUPPRESSED), "memory_type": "conversation"})
    store._conn.execute("UPDATE vectors SET quality = 'suppressed'")
    store._conn.commit()
    store.close()
    return root


def _memory_db(vault: Path) -> Path:
    return vault / "embeddings" / "memory.db"


def _rows(vault: Path, db_name: str) -> list[tuple[str, str]]:
    db = vault / "embeddings" / db_name
    if not db.exists():
        return []
    conn = sqlite3.connect(str(db))
    try:
        return sorted(conn.execute("SELECT text, memory_type FROM vectors").fetchall())
    finally:
        conn.close()


def run(vault: Path, confirm: bool) -> dict:
    with patch("src.ingest.writers.embed_text", side_effect=fake_embed):
        return backfill.backfill(vault, confirm=confirm)


class TestDryRun:
    def test_dry_run_reports_the_plan_and_writes_nothing(self, vault):
        before = (_rows(vault, "memory.db"), _rows(vault, "ingested.db"))

        report = run(vault, confirm=False)

        assert report["indexed"] == {"ingested.db": 1, "memory.db": 1}
        assert report["already_indexed"] == 1
        assert report["assistant_gated"] == 1
        assert (_rows(vault, "memory.db"), _rows(vault, "ingested.db")) == before
        assert not (vault / "embeddings" / "ingested.db").exists()

    def test_control_confirm_on_the_same_vault_writes(self, vault):
        run(vault, confirm=True)
        assert _rows(vault, "ingested.db") == [(DOC, "ingested")]


class TestConfirm:
    def test_missing_chunks_are_indexed_where_retrieval_reads(self, vault):
        report = run(vault, confirm=True)

        assert report["indexed"] == {"ingested.db": 1, "memory.db": 1}
        assert _rows(vault, "ingested.db") == [(DOC, "ingested")]
        # The user turn is added as conversation; the suppressed turn is not
        # added a second time; the assistant turn is not indexed (ADR-033).
        assert _rows(vault, "memory.db") == sorted([(SUPPRESSED, "conversation"),
                                                    (USER, "conversation")])

    def test_a_second_confirm_adds_nothing(self, vault):
        run(vault, confirm=True)
        after_first = (_rows(vault, "memory.db"), _rows(vault, "ingested.db"))

        report = run(vault, confirm=True)

        assert report["indexed"] == {"ingested.db": 0, "memory.db": 0}
        assert report["already_indexed"] == 3
        assert (_rows(vault, "memory.db"), _rows(vault, "ingested.db")) == after_first

    def test_json_index_cross_check(self, vault):
        report = run(vault, confirm=True)

        assert report["json_index"] == {
            "entries": 3, "with_chunk_file": 2, "without_chunk_file": 1,
            "covered": 2, "readable": True,
        }


class TestWriteMemoryRecords:
    """/ingest/json writes `ingested` records through write_memory: canonical
    files with `text` and `id` instead of `content` and `chunk_id`, which hit
    the same unread JSON index."""

    def _record(self, vault: Path, record_id: str, text: str) -> None:
        safe_write_json(vault / "memory" / "ingested" / f"{record_id}.json", {
            "id": record_id, "timestamp": record_id, "type": "ingested", "text": text,
            "normalized_text": text.lower(), "source": "json_import", "tags": [], "metadata": {},
        })

    def test_record_is_indexed_into_ingested_db(self, vault):
        text = "A synthetic imported note about mending fishing nets before the season opens."
        self._record(vault, "2026-01-05T09-00-00-000001", text)

        report = run(vault, confirm=True)

        assert (text, "ingested") in _rows(vault, "ingested.db")
        assert report["unreadable_chunk_files"] == 0

    def test_record_the_index_filter_rejects_is_not_indexed(self, vault):
        """should_index, as on the live write path. Positive control: the
        test above, where a record that passes the filter is indexed."""
        self._record(vault, "2026-01-05T09-00-00-000002", "too short to index")

        report = run(vault, confirm=True)

        assert ("too short to index", "ingested") not in _rows(vault, "ingested.db")
        assert report["filtered"] == 1


class TestLiveVaultGuard:
    def test_confirm_refuses_a_vault_that_is_not_the_test_vault(self, vault, tmp_path, monkeypatch):
        other = tmp_path / "other_test_vault"
        other.mkdir()
        monkeypatch.setenv("VAULT_PATH_TEST", str(other))

        with patch("src.ingest.writers.embed_text", side_effect=fake_embed):
            code = backfill.main(["--vault", str(vault), "--confirm"])

        assert code == 2
        assert not (vault / "embeddings" / "ingested.db").exists()

    def test_control_confirm_runs_on_the_test_vault(self, vault, monkeypatch):
        monkeypatch.setenv("VAULT_PATH_TEST", str(vault))

        with patch("src.ingest.writers.embed_text", side_effect=fake_embed):
            code = backfill.main(["--vault", str(vault), "--confirm"])

        assert code == 0
        assert _rows(vault, "ingested.db") == [(DOC, "ingested")]

    def test_dry_run_needs_no_test_vault(self, vault, monkeypatch):
        monkeypatch.delenv("VAULT_PATH_TEST", raising=False)
        assert backfill.main(["--vault", str(vault)]) == 0
