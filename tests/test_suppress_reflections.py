"""
tests/test_suppress_reflections.py

Tests for tools/suppress_reflections.py.

After the JSON_INDEX_TYPES cleanup, profile/reflection/journal records
are no longer indexed in JSON files - memory.db (SQLite) is the sole
backend. The suppression tool was migrated to operate on memory.db
directly: junk records are hard-deleted from the SQLite vector store
while their canonical JSON records remain in place with
metadata.quality = "suppressed".

Test verifies the contract:
  - Junk record is REMOVED from memory.db (not just filtered).
  - Canonical JSON record is FLAGGED but preserved (append-only rule).
  - Non-junk records are untouched in both stores.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.retrieval.sqlite_vector_store import SqliteVectorStore


# Synthetic embedding dimension - small for fast tests; SqliteVectorStore
# does not validate dimension.
_FAKE_EMBED_DIM = 8
_FAKE_EMBEDDING = [0.1] * _FAKE_EMBED_DIM


def _build_vault(tmp_path: Path) -> Path:
    """Create a minimal test vault with reflection dir and embeddings dir."""
    vault = tmp_path / "vault"
    (vault / "memory" / "reflection").mkdir(parents=True)
    (vault / "embeddings").mkdir(parents=True)
    return vault


def _write_canonical(reflection_dir: Path, record: dict) -> Path:
    """Write a canonical reflection JSON record to disk."""
    path = reflection_dir / f"{record['id']}.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def _seed_memory_db(db_path: Path, records: list[dict]) -> None:
    """Insert records into memory.db so the tool has rows to delete."""
    store = SqliteVectorStore(db_path)
    try:
        for record in records:
            store.insert({
                "id": record["id"],
                "text": record["text"],
                "embedding": _FAKE_EMBEDDING,
                "memory_type": "reflection",
                "created_at": record["timestamp"],
                "metadata": {},
            })
    finally:
        store.close()


def test_suppress_removes_junk_from_memory_db_and_flags_canonical(
    tmp_path, monkeypatch,
):
    """End-to-end: suppress reads canonical, flags junk in JSON, and
    hard-deletes the corresponding row from memory.db."""
    vault = _build_vault(tmp_path)
    reflection_dir = vault / "memory" / "reflection"
    db_path = vault / "embeddings" / "memory.db"

    # Junk text matches the "i'm here to help" JUNK_MARKER in
    # tools/audit_reflections.is_junk_reflection. Padded over 100 chars
    # to clear the minimum-length filter.
    junk_record = {
        "id": "2026-05-13T10-00-00",
        "timestamp": "2026-05-13T10-00-00",
        "type": "reflection",
        "text": (
            "I'm here to help with whatever you would like to discuss. "
            "Let me clarify what I can do for you in this session."
        ),
        "source": "reflection_engine",
        "tags": [],
        "metadata": {},
    }

    # Good text: long enough, no junk markers, not dominated by I-sentences.
    good_record = {
        "id": "2026-05-13T11-00-00",
        "timestamp": "2026-05-13T11-00-00",
        "type": "reflection",
        "text": (
            "Across the last three weeks the user shifted attention from "
            "infrastructure work to interface design, with a steady cadence "
            "of evening sessions and longer focus windows on weekends."
        ),
        "source": "reflection_engine",
        "tags": [],
        "metadata": {},
    }

    _write_canonical(reflection_dir, junk_record)
    _write_canonical(reflection_dir, good_record)
    _seed_memory_db(db_path, [junk_record, good_record])

    # Sanity precondition.
    pre = SqliteVectorStore(db_path)
    try:
        assert pre.count() == 2
    finally:
        pre.close()

    # Point the tool at our temp vault. The tool imports
    # get_private_vault_path with `from x import y`, so we patch the
    # symbol in the tool's namespace.
    import tools.suppress_reflections as suppress_mod

    monkeypatch.setattr(suppress_mod, "get_private_vault_path", lambda: vault)

    total, suppressed, _ = suppress_mod.suppress_reflections()

    assert total == 2
    assert suppressed == 1

    # memory.db must reflect the hard-delete.
    after = SqliteVectorStore(db_path)
    try:
        assert after.count() == 1, (
            "Junk reflection should have been hard-deleted from memory.db"
        )
        rows = list(after._conn.execute("SELECT id FROM vectors"))
        remaining_ids = [r[0] for r in rows]
        assert remaining_ids == [good_record["id"]]
    finally:
        after.close()

    # Canonical junk record stays on disk, now flagged. Append-only.
    junk_after = json.loads(
        (reflection_dir / f"{junk_record['id']}.json").read_text(encoding="utf-8")
    )
    assert junk_after["metadata"]["quality"] == "suppressed"
    assert "suppressed_reason" in junk_after["metadata"]

    # Canonical good record unchanged.
    good_after = json.loads(
        (reflection_dir / f"{good_record['id']}.json").read_text(encoding="utf-8")
    )
    assert good_after.get("metadata", {}).get("quality") != "suppressed"


def test_suppress_does_not_touch_stale_reflection_index_json(
    tmp_path, monkeypatch,
):
    """After migration the tool must leave any stale
    vault/embeddings/reflection_index.json alone. Matches the B-RET-001
    precedent on conversation_index.json: stale JSON indexes in user
    vaults are user data; the tool's responsibility is memory.db only."""
    vault = _build_vault(tmp_path)
    reflection_dir = vault / "memory" / "reflection"
    db_path = vault / "embeddings" / "memory.db"
    json_index_path = vault / "embeddings" / "reflection_index.json"

    junk_record = {
        "id": "2026-05-13T10-00-00",
        "timestamp": "2026-05-13T10-00-00",
        "type": "reflection",
        "text": (
            "I'm here to help. How can I assist you today with your goals "
            "and what would you like to discuss in this session please?"
        ),
        "source": "reflection_engine",
        "tags": [],
        "metadata": {},
    }
    _write_canonical(reflection_dir, junk_record)
    _seed_memory_db(db_path, [junk_record])

    # Pre-seed a stale JSON index containing an entry for the junk record.
    # The pre-migration tool would have filtered this out and rewritten
    # the file; the post-migration tool must NOT touch it.
    stale_index_payload = [
        {
            "id": junk_record["id"],
            "file_path": str(reflection_dir / f"{junk_record['id']}.json"),
            "text_preview": junk_record["text"][:80],
            "embedding": _FAKE_EMBEDDING,
        },
    ]
    json_index_path.write_text(
        json.dumps(stale_index_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    original_bytes = json_index_path.read_bytes()

    import tools.suppress_reflections as suppress_mod

    monkeypatch.setattr(suppress_mod, "get_private_vault_path", lambda: vault)

    suppress_mod.suppress_reflections()

    assert json_index_path.exists(), (
        "Tool must not delete a pre-existing JSON index file - user data."
    )
    assert json_index_path.read_bytes() == original_bytes, (
        "Tool must leave the stale JSON index byte-for-byte unchanged."
    )
