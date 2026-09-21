"""
tests/test_id_collision.py

Issue #210: a second record claiming an id already in memory.db was
discarded, and nothing said so.

`write_memory` derives the record id from the timestamp alone while the
directory carries the type, so a conversation and a reflection written in
the same second produced two canonical files and one index row. The vault
looked correct -- both files present, append-only respected -- while one of
the records was unreachable by retrieval. Three such pairs exist in the live
vault, all from before `_next_timestamp()` moved to microsecond precision.

The fix is at the store, not the id minter: an id minter can always be
raced, and ids that came from outside this process (imports, rebuilds,
another machine) are not covered by any minter at all.

All fixtures here are synthetic (CLAUDE.md Vault Privacy Rule).
"""

import sqlite3

import pytest

from src.retrieval.sqlite_vector_store import ID_COLLISION_MARKER, SqliteVectorStore

EMBED_DIM = 8


def _record(record_id: str, text: str, memory_type: str = "conversation") -> dict:
    return {
        "id": record_id,
        "text": text,
        "embedding": [0.1] * EMBED_DIM,
        "source": "chat",
        "memory_type": memory_type,
        "created_at": "2024-01-01T00-00-01",
        "metadata": {"role": "user"},
    }


@pytest.fixture
def store(tmp_path):
    s = SqliteVectorStore(tmp_path / "memory.db")
    yield s
    s.close()


def _rows(store) -> dict[str, str]:
    return {
        row["id"]: row["text"]
        for row in store._conn.execute("SELECT id, text FROM vectors")
    }


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

def test_a_colliding_record_is_not_discarded(store):
    """The regression test. Before the fix this ended with one row."""
    store.insert(_record("2024-01-01T00-00-01", "a conversation turn"))
    store.insert(_record("2024-01-01T00-00-01", "a reflection body", "reflection"))

    rows = _rows(store)
    assert len(rows) == 2
    assert set(rows.values()) == {"a conversation turn", "a reflection body"}


def test_the_collided_record_is_reachable_by_search(store):
    """Reachability, not just row count. A row nothing can address is the
    same defect wearing a different number."""
    store.insert(_record("2024-01-01T00-00-01", "a conversation turn"))
    second_id = store.insert(
        _record("2024-01-01T00-00-01", "a reflection body", "reflection")
    )

    results = store.search([0.1] * EMBED_DIM, limit=10)
    assert second_id in {r["id"] for r in results}


def test_the_first_record_keeps_its_canonical_id(store):
    first = store.insert(_record("2024-01-01T00-00-01", "a conversation turn"))
    second = store.insert(
        _record("2024-01-01T00-00-01", "a reflection body", "reflection")
    )

    assert first == "2024-01-01T00-00-01"
    assert second == f"2024-01-01T00-00-01{ID_COLLISION_MARKER}2"


def test_a_third_record_on_the_same_id_gets_the_next_suffix(store):
    store.insert(_record("2024-01-01T00-00-01", "first body"))
    store.insert(_record("2024-01-01T00-00-01", "second body"))
    third = store.insert(_record("2024-01-01T00-00-01", "third body"))

    assert third == f"2024-01-01T00-00-01{ID_COLLISION_MARKER}3"
    assert len(_rows(store)) == 3


def test_the_collision_is_not_silent(store, caplog):
    store.insert(_record("2024-01-01T00-00-01", "a conversation turn"))
    with caplog.at_level("WARNING"):
        store.insert(_record("2024-01-01T00-00-01", "a reflection body", "reflection"))

    assert any("collision" in r.message.lower() for r in caplog.records)


def test_the_warning_carries_no_vault_content(store, caplog):
    """CLAUDE.md Vault Privacy Rule: record ids and record text are vault
    content and must not reach the log."""
    store.insert(_record("2024-01-01T00-00-01", "a conversation turn"))
    with caplog.at_level("WARNING"):
        store.insert(_record("2024-01-01T00-00-01", "a reflection body", "reflection"))

    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "2024-01-01T00-00-01" not in logged
    assert "reflection body" not in logged


def test_each_record_keeps_its_own_columns(store):
    store.insert(_record("2024-01-01T00-00-01", "a conversation turn"))
    store.insert(_record("2024-01-01T00-00-01", "a reflection body", "reflection"))

    types = {
        row["id"]: row["memory_type"]
        for row in store._conn.execute("SELECT id, memory_type FROM vectors")
    }
    assert set(types.values()) == {"conversation", "reflection"}


# ---------------------------------------------------------------------------
# Re-indexing the same record must stay idempotent
# ---------------------------------------------------------------------------

def test_reinserting_the_same_record_is_a_no_op(store):
    """Rebuilds and backfills re-insert rows they have already written. That
    is not a collision and must not fan out into duplicate rows."""
    first = store.insert(_record("2024-01-01T00-00-01", "a conversation turn"))
    again = store.insert(_record("2024-01-01T00-00-01", "a conversation turn"))

    assert again == first
    assert len(_rows(store)) == 1


def test_whitespace_and_case_differences_are_still_the_same_record(store):
    store.insert(_record("2024-01-01T00-00-01", "a conversation turn"))
    store.insert(_record("2024-01-01T00-00-01", "A  Conversation\n turn "))

    assert len(_rows(store)) == 1


def test_distinct_ids_are_untouched(store):
    store.insert(_record("2024-01-01T00-00-01", "first body"))
    store.insert(_record("2024-01-01T00-00-02", "second body"))

    assert set(_rows(store)) == {"2024-01-01T00-00-01", "2024-01-01T00-00-02"}


# ---------------------------------------------------------------------------
# The write path end to end
# ---------------------------------------------------------------------------

def test_write_memory_indexes_a_colliding_record(tmp_path, monkeypatch):
    """The live path, with the id minter forced to repeat itself.

    _next_timestamp() spins at microsecond precision so this cannot happen
    by accident any more; pinning it is how the store-level guarantee gets
    tested rather than assumed.
    """
    from src.memory import write_memory as wm

    vault = tmp_path / "vault"
    (vault / "embeddings").mkdir(parents=True)
    monkeypatch.setattr(wm, "get_private_vault_path", lambda: vault)
    monkeypatch.setattr(wm.storage, "get_memory_dir", lambda v, t: _memory_dir(v, t))
    monkeypatch.setattr(wm, "embed_text", lambda text: [0.1] * EMBED_DIM)
    monkeypatch.setattr(wm, "_next_timestamp", lambda: "2024-01-01T00-00-01")
    monkeypatch.setattr(wm, "should_index_record", lambda *a, **k: True)
    monkeypatch.setattr(wm, "vault_writes_blocked", lambda: None)

    wm.write_memory(
        text="a conversation turn long enough to clear the content floor here",
        memory_type="conversation",
        source="chat",
    )
    wm.write_memory(
        text="a reflection body long enough to clear the content floor as well",
        memory_type="reflection",
        source="reflection_engine",
    )

    conn = sqlite3.connect(vault / "embeddings" / "memory.db")
    try:
        rows = conn.execute("SELECT id, memory_type FROM vectors").fetchall()
    finally:
        conn.close()

    assert len(rows) == 2, "the second record was dropped on the live write path"
    assert {r[1] for r in rows} == {"conversation", "reflection"}


def _memory_dir(vault, memory_type):
    path = vault / "memory" / memory_type
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# Guard against reintroduction
# ---------------------------------------------------------------------------

def test_insert_does_not_use_insert_or_ignore():
    """INSERT OR IGNORE is what made the loss silent. Re-adding it would make
    every test above pass anyway for the idempotent case, so the ban is
    stated directly."""
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "src" / "retrieval" / "sqlite_vector_store.py"
    ).read_text(encoding="utf-8")
    body = source.split("def insert(", 1)[1].split("\n    def ", 1)[0]
    # The docstring explains what was removed and why, so it is not code.
    body = body.split('"""')[2] if body.count('"""') >= 2 else body
    assert "INSERT OR IGNORE" not in body
    assert "INSERT OR REPLACE" not in body, "replacing is the same loss, louder"
