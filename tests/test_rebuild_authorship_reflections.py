"""
tests/test_rebuild_authorship_reflections.py

Tests for scripts/rebuild_authorship_reflections.py (Recalling Too Well
Phase 1, item 3): backfills the authorship column for existing reflection
rows in memory.db only, leaving other memory_types untouched.
"""

from __future__ import annotations

import json
import sqlite3
import struct

import pytest

from scripts.rebuild_authorship_reflections import rebuild


def _make_db(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE vectors (
            id          TEXT PRIMARY KEY,
            text        TEXT NOT NULL,
            embedding   BLOB NOT NULL,
            source      TEXT,
            memory_type TEXT,
            created_at  TEXT,
            metadata    TEXT,
            authorship  TEXT DEFAULT 'unknown'
        )
        """
    )
    blob = struct.pack("1f", 0.0)

    def _insert(rec_id, source, memory_type, metadata=None):
        conn.execute(
            "INSERT INTO vectors (id, text, embedding, source, memory_type, created_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rec_id, "some text", blob, source, memory_type, "2026-09-01T00-00-00",
             json.dumps(metadata or {})),
        )

    _insert("refl-1", "reflection_engine", "reflection")
    _insert("refl-2", "session_reflection", "reflection")
    _insert("refl-3", "unrecognized_source", "reflection")
    _insert("convo-1", "chat", "conversation", {"role": "user"})
    _insert("journal-1", "api", "journal")

    conn.commit()
    conn.close()


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "memory.db"
    _make_db(p)
    return p


def _authorship_of(db_path, rec_id):
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT authorship FROM vectors WHERE id = ?", (rec_id,)).fetchone()
    conn.close()
    return row[0]


class TestDryRun:
    def test_dry_run_does_not_write(self, db_path):
        counts = rebuild(db_path, confirm=False)
        assert counts == {"first_person": 2, "unknown": 1}
        # Column untouched -- still the schema default.
        assert _authorship_of(db_path, "refl-1") == "unknown"
        assert _authorship_of(db_path, "refl-2") == "unknown"
        assert _authorship_of(db_path, "refl-3") == "unknown"


class TestConfirm:
    def test_confirm_writes_expected_labels(self, db_path):
        rebuild(db_path, confirm=True)
        assert _authorship_of(db_path, "refl-1") == "first_person"
        assert _authorship_of(db_path, "refl-2") == "first_person"
        assert _authorship_of(db_path, "refl-3") == "unknown"

    def test_confirm_does_not_touch_non_reflection_rows(self, db_path):
        rebuild(db_path, confirm=True)
        # conversation/journal rows were never selected by the query at
        # all -- still at the schema default, not reclassified.
        assert _authorship_of(db_path, "convo-1") == "unknown"
        assert _authorship_of(db_path, "journal-1") == "unknown"


class TestMissingDb:
    def test_missing_db_returns_empty(self, tmp_path):
        missing = tmp_path / "does_not_exist.db"
        assert rebuild(missing, confirm=True) == {}
