"""
tests/test_sqlite_insert_if_absent.py

SqliteVectorStore.insert_if_absent: the content-key check and the write in
one transaction under the store lock, used by the ingest writer (#174, and
the lean review of #315: findings 1, 4 and 5, unverified item U1). Plus the
content_key column and its migration.

All content is synthetic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.retrieval.sqlite_vector_store import (
    IndexOutcome,
    SqliteVectorStore,
    content_key,
    read_index_snapshot,
)

OLD = "The old revision of a synthetic paragraph about tide tables."
NEW = "The new revision of a synthetic paragraph about harbour lights."
EMB = [0.1, 0.2, 0.3]


@pytest.fixture
def store(tmp_path: Path):
    s = SqliteVectorStore(tmp_path / "ingested.db")
    yield s
    s.close()


def record(record_id: str, text: str, embedding=EMB) -> dict:
    r = {"id": record_id, "text": text, "memory_type": "ingested", "source": "file"}
    if embedding is not None:
        r["embedding"] = embedding
    return r


def fresh_rows(db: Path) -> dict:
    """Rows as a second connection sees them: only what was committed."""
    conn = sqlite3.connect(str(db))
    try:
        return dict(conn.execute("SELECT id, text FROM vectors").fetchall())
    finally:
        conn.close()


class TestOutcomes:
    def test_absent_text_without_embedding_asks_for_one_and_writes_nothing(self, store):
        assert store.insert_if_absent(record("a", OLD, None)) is IndexOutcome.NEEDS_EMBEDDING
        assert store.count() == 0

    def test_absent_text_with_embedding_is_inserted_with_its_key(self, store):
        assert store.insert_if_absent(record("a", OLD)) is IndexOutcome.INSERTED
        assert store.content_keys() == {content_key(OLD)}

    def test_same_id_same_text_is_already_indexed(self, store):
        store.insert_if_absent(record("a", OLD))
        assert store.insert_if_absent(record("a", OLD.upper(), None)) is IndexOutcome.ALREADY_INDEXED

    def test_text_under_another_id_is_already_indexed(self, store):
        store.insert_if_absent(record("a", OLD))
        assert store.insert_if_absent(record("b", OLD, None)) is IndexOutcome.ALREADY_INDEXED
        assert fresh_rows(store.db_path) == {"a": OLD}

    def test_dedupe_type_limits_the_check_to_one_memory_type(self, store):
        store.insert({"id": "p", "text": OLD, "embedding": EMB, "memory_type": "profile"})
        outcome = store.insert_if_absent(
            {**record("c", OLD), "memory_type": "conversation"}, dedupe_type="conversation",
        )
        assert outcome is IndexOutcome.INSERTED


class TestReplaceIsOneTransaction:
    """Finding 5: the delete of the old row and the insert of the new one
    commit together or not at all."""

    def test_an_insert_failure_after_the_delete_rolls_the_delete_back(self, store):
        store.insert_if_absent(record("a", OLD))
        broken = {**record("a", NEW), "created_at": object()}  # unbindable: fails in INSERT

        with pytest.raises(sqlite3.Error):
            store.insert_if_absent(broken)
        # Any later commit on the shared connection must not carry the delete.
        store.insert_if_absent(record("b", "An unrelated synthetic sentence about rope."))

        assert fresh_rows(store.db_path)["a"] == OLD

    def test_an_unencodable_record_raises_before_anything_is_deleted(self, store):
        store.insert_if_absent(record("a", OLD))

        with pytest.raises(Exception):
            store.insert_if_absent(record("a", NEW, embedding=[None, 0.2]))
        store.insert_if_absent(record("b", "An unrelated synthetic sentence about sails."))

        assert fresh_rows(store.db_path)["a"] == OLD

    def test_control_a_successful_swap_replaces_the_row(self, store):
        store.insert_if_absent(record("a", OLD))
        assert store.insert_if_absent(record("a", NEW)) is IndexOutcome.INSERTED
        assert fresh_rows(store.db_path) == {"a": NEW}


def _set(store, column: str, value, row_id: str = "a") -> None:
    store._conn.execute(f"UPDATE vectors SET {column} = ? WHERE id = ?", (value, row_id))
    store._conn.commit()


def _get(store, column: str, row_id: str = "a"):
    return store._conn.execute(f"SELECT {column} FROM vectors WHERE id = ?", (row_id,)).fetchone()[0]


class TestWhatASwapCarries:
    """U1: suppression is an operator judgment and follows the id; delivery
    history measured the old text and does not."""

    def test_suppression_is_carried_and_history_is_not(self, store):
        store.insert_if_absent(record("a", OLD))
        _set(store, "quality", "suppressed")
        _set(store, "frequency_score", 4.5)

        store.insert_if_absent(record("a", NEW))

        assert _get(store, "quality") == "suppressed"
        assert _get(store, "frequency_score") == 0.0

    def test_control_an_unsuppressed_row_stays_unsuppressed(self, store):
        store.insert_if_absent(record("a", OLD))
        store.insert_if_absent(record("a", NEW))
        assert _get(store, "quality") is None


LEGACY_SCHEMA = """CREATE TABLE vectors (
    id TEXT PRIMARY KEY, text TEXT NOT NULL, embedding BLOB NOT NULL,
    source TEXT, memory_type TEXT, created_at TEXT, metadata TEXT)"""


class TestContentKeyMigration:
    def test_opening_a_legacy_store_fills_every_key_and_indexes_the_column(self, tmp_path):
        db = tmp_path / "memory.db"
        conn = sqlite3.connect(str(db))
        conn.execute(LEGACY_SCHEMA)
        conn.executemany(
            "INSERT INTO vectors (id, text, embedding) VALUES (?, ?, x'00')",
            [(f"r{i}", f"Synthetic legacy row number {i}.") for i in range(5)],
        )
        conn.commit()
        conn.close()

        store = SqliteVectorStore(db)
        try:
            keys = dict(store._conn.execute("SELECT id, content_key FROM vectors").fetchall())
            indexes = {r[1] for r in store._conn.execute("PRAGMA index_list(vectors)")}
        finally:
            store.close()

        assert keys == {f"r{i}": content_key(f"Synthetic legacy row number {i}.") for i in range(5)}
        assert "idx_vectors_content_key" in indexes

    def test_control_insert_writes_the_key_itself(self, store):
        store.insert({"id": "x", "text": OLD, "embedding": EMB})
        assert _get(store, "content_key", "x") == content_key(OLD)


class TestReadIndexSnapshot:
    def test_missing_database_is_empty_and_not_created(self, tmp_path):
        assert read_index_snapshot(tmp_path / "absent.db") == []
        assert not (tmp_path / "absent.db").exists()

    def test_rows_are_read_with_keys_computed_from_text(self, store):
        store.insert({"id": "x", "text": OLD, "embedding": EMB, "memory_type": "ingested"})
        assert read_index_snapshot(store.db_path) == [("x", "ingested", content_key(OLD))]
