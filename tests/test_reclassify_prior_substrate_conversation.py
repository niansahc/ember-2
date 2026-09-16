"""
tests/test_reclassify_prior_substrate_conversation.py

Tests for scripts/reclassify_prior_substrate_conversation.py (ADR-015
amendment, implementation step 5): moves the prior-substrate ChatGPT
conversation corpus (memory_type='ingested', source='chatgpt') from
ingested.db to memory.db as memory_type='conversation', with role-based
authorship and every other column carried over verbatim.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.reclassify_prior_substrate_conversation import reclassify
from src.retrieval.sqlite_vector_store import SqliteVectorStore


def _insert(store: SqliteVectorStore, rec_id: str, source: str, memory_type: str, metadata: dict, quality: str | None = None) -> None:
    store.insert({
        "id": rec_id,
        "text": f"content for {rec_id}",
        "embedding": [0.1, 0.2, 0.3],
        "source": source,
        "memory_type": memory_type,
        "created_at": "1670774793.776416",
        "metadata": metadata,
    })
    if quality is not None:
        conn = sqlite3.connect(str(store.db_path))
        try:
            conn.execute("ALTER TABLE vectors ADD COLUMN quality TEXT")
        except sqlite3.OperationalError:
            pass
        conn.execute("UPDATE vectors SET quality = ? WHERE id = ?", (quality, rec_id))
        conn.commit()
        conn.close()


@pytest.fixture
def dbs(tmp_path):
    ingested_db = tmp_path / "ingested.db"
    memory_db = tmp_path / "memory.db"

    store = SqliteVectorStore(ingested_db)
    _insert(store, "chatgpt-user-1", "chatgpt", "ingested", {"role": "user"}, quality="ok")
    _insert(store, "chatgpt-assistant-1", "chatgpt", "ingested", {"role": "assistant"}, quality="suppressed")
    _insert(store, "chatgpt-user-2", "chatgpt", "ingested", {"role": "user"}, quality="ok")
    # Genuine third-party material -- must NOT be touched.
    _insert(store, "pdf-1", "pdf", "ingested", {}, quality="ok")
    # Same source, but role fails detection -- must NOT be migrated, and
    # must be counted, not guessed.
    _insert(store, "chatgpt-bad-role", "chatgpt", "ingested", {"role": "narrator"}, quality="ok")
    store.close()

    # Pre-existing native memory.db content, untouched by the migration.
    memory_store = SqliteVectorStore(memory_db)
    _insert(memory_store, "native-convo-1", "chat", "conversation", {"role": "user"})
    memory_store.close()

    return ingested_db, memory_db


def _memory_type_of(db_path, rec_id):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT memory_type FROM vectors WHERE id = ?", (rec_id,)).fetchone()
    conn.close()
    return row[0] if row else None


def _row(db_path, rec_id):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM vectors WHERE id = ?", (rec_id,)).fetchone()
    conn.close()
    return row


class TestDryRun:
    def test_dry_run_writes_nothing(self, dbs):
        ingested_db, memory_db = dbs
        report = reclassify(ingested_db, memory_db, confirm=False)

        assert report["migrated_user"] == 2
        assert report["migrated_assistant"] == 1
        assert report["skipped_unresolvable_role"] == 1

        # Still in ingested.db, unchanged.
        assert _memory_type_of(ingested_db, "chatgpt-user-1") == "ingested"
        assert _memory_type_of(ingested_db, "chatgpt-assistant-1") == "ingested"
        assert _row(ingested_db, "chatgpt-user-1") is not None

        # Not present in memory.db.
        assert _row(memory_db, "chatgpt-user-1") is None

    def test_dry_run_reports_accurate_before_after(self, dbs):
        ingested_db, memory_db = dbs
        report = reclassify(ingested_db, memory_db, confirm=False)

        assert report["before"]["ingested_db"] == {"ingested": 5}
        assert report["before"]["memory_db"] == {"conversation": 1}
        # "after" reflects what WOULD happen, computed without writing.
        assert report["after"]["ingested_db"] == {"ingested": 2}  # pdf-1 + chatgpt-bad-role
        assert report["after"]["memory_db"] == {"conversation": 4}  # 1 native + 3 migrated


class TestConfirm:
    def test_confirm_moves_qualifying_rows(self, dbs):
        ingested_db, memory_db = dbs
        reclassify(ingested_db, memory_db, confirm=True)

        for rec_id in ("chatgpt-user-1", "chatgpt-assistant-1", "chatgpt-user-2"):
            assert _row(ingested_db, rec_id) is None
            moved = _row(memory_db, rec_id)
            assert moved is not None
            assert moved["memory_type"] == "conversation"

    def test_confirm_leaves_non_chatgpt_ingested_untouched(self, dbs):
        ingested_db, memory_db = dbs
        reclassify(ingested_db, memory_db, confirm=True)

        assert _row(ingested_db, "pdf-1") is not None
        assert _memory_type_of(ingested_db, "pdf-1") == "ingested"
        assert _row(memory_db, "pdf-1") is None

    def test_confirm_leaves_unresolvable_role_as_ingested(self, dbs):
        """Regression test: a record that fails role detection is never
        silently defaulted -- it stays exactly where it was."""
        ingested_db, memory_db = dbs
        reclassify(ingested_db, memory_db, confirm=True)

        assert _memory_type_of(ingested_db, "chatgpt-bad-role") == "ingested"
        assert _row(memory_db, "chatgpt-bad-role") is None

    def test_confirm_assigns_authorship_by_role(self, dbs):
        ingested_db, memory_db = dbs
        reclassify(ingested_db, memory_db, confirm=True)

        user_row = _row(memory_db, "chatgpt-user-1")
        assistant_row = _row(memory_db, "chatgpt-assistant-1")
        assert user_row["authorship"] == "first_person"
        assert assistant_row["authorship"] == "mixed"

    def test_confirm_preserves_quality_suppression(self, dbs):
        """3,592 of the real corpus's rows are already quality='suppressed'.
        Losing this on migration would silently un-suppress them."""
        ingested_db, memory_db = dbs
        reclassify(ingested_db, memory_db, confirm=True)

        suppressed_row = _row(memory_db, "chatgpt-assistant-1")
        assert suppressed_row["quality"] == "suppressed"

        # search() on the destination store must honor it -- confirms the
        # `quality` column carried over is the SAME column search() checks,
        # not a same-named-but-disconnected copy.
        store = SqliteVectorStore(memory_db)
        results = store.search([0.1, 0.2, 0.3], limit=10, memory_type="conversation")
        store.close()
        returned_ids = {r["id"] for r in results}
        assert "chatgpt-assistant-1" not in returned_ids
        assert "chatgpt-user-1" in returned_ids

    def test_confirm_preserves_tier_and_heat_verbatim(self, dbs):
        """created_at/tier/heat_score/last_retrieved_at must be carried
        over unchanged -- the migration reclassifies type, it does not
        reset retrieval history or recompute tiering."""
        ingested_db, memory_db = dbs

        conn = sqlite3.connect(str(ingested_db))
        conn.execute(
            "UPDATE vectors SET tier = 'warm', heat_score = 0.33, "
            "last_retrieved_at = '2026-01-01T00-00-00' WHERE id = 'chatgpt-user-1'"
        )
        conn.commit()
        conn.close()

        reclassify(ingested_db, memory_db, confirm=True)

        moved = _row(memory_db, "chatgpt-user-1")
        assert moved["tier"] == "warm"
        assert moved["heat_score"] == pytest.approx(0.33)
        assert moved["last_retrieved_at"] == "2026-01-01T00-00-00"
        assert moved["created_at"] == "1670774793.776416"

    def test_confirm_is_idempotent(self, dbs):
        ingested_db, memory_db = dbs
        reclassify(ingested_db, memory_db, confirm=True)
        second_report = reclassify(ingested_db, memory_db, confirm=True)

        assert second_report["migrated_user"] == 0
        assert second_report["migrated_assistant"] == 0
        # Nothing duplicated in memory.db.
        conn = sqlite3.connect(str(memory_db))
        count = conn.execute(
            "SELECT COUNT(*) FROM vectors WHERE id = 'chatgpt-user-1'"
        ).fetchone()[0]
        conn.close()
        assert count == 1


class TestMissingDb:
    def test_missing_ingested_db_returns_empty(self, tmp_path):
        missing = tmp_path / "does_not_exist.db"
        memory_db = tmp_path / "memory.db"
        report = reclassify(missing, memory_db, confirm=True)
        assert report["migrated_user"] == 0
        assert report["migrated_assistant"] == 0
