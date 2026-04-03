"""
Tests for SQLite index migration (conversation, profile, reflection, journal).

Covers: migration correctness, JSON archival, profile retrieval after
migration, write path routing to SQLite for migrated types.
"""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from src.retrieval.sqlite_vector_store import SqliteVectorStore


# ── Migration correctness ───────────────────────────────────────────────


def test_sqlite_store_inserts_and_searches(tmp_path: Path) -> None:
    """Verify SqliteVectorStore works for non-ingested memory types."""
    db_path = tmp_path / "memory.db"
    store = SqliteVectorStore(db_path)

    embedding = [0.1] * 768
    store.insert({
        "id": "test-conv-1",
        "text": "This is a conversation record for testing",
        "embedding": embedding,
        "source": "api",
        "memory_type": "conversation",
        "created_at": "2026-04-03T10-00-00",
        "metadata": {"role": "user"},
    })

    results = store.search(
        query_embedding=embedding,
        limit=5,
        memory_type="conversation",
    )

    assert len(results) >= 1
    assert results[0]["content"] == "This is a conversation record for testing"
    assert results[0]["memory_type"] == "conversation"
    store.close()


def test_sqlite_store_filters_by_memory_type(tmp_path: Path) -> None:
    """Verify memory_type filtering works in SQLite search."""
    db_path = tmp_path / "memory.db"
    store = SqliteVectorStore(db_path)

    embedding = [0.1] * 768
    store.insert({
        "id": "conv-1",
        "text": "Conversation record",
        "embedding": embedding,
        "source": "api",
        "memory_type": "conversation",
        "created_at": "2026-04-03",
        "metadata": {},
    })
    store.insert({
        "id": "prof-1",
        "text": "Profile record about the user",
        "embedding": embedding,
        "source": "api",
        "memory_type": "profile",
        "created_at": "2026-04-03",
        "metadata": {},
    })

    conv_results = store.search(embedding, limit=10, memory_type="conversation")
    prof_results = store.search(embedding, limit=10, memory_type="profile")

    conv_types = {r["memory_type"] for r in conv_results}
    prof_types = {r["memory_type"] for r in prof_results}

    assert conv_types == {"conversation"}
    assert prof_types == {"profile"}
    store.close()


def test_migration_preserves_record_count(tmp_path: Path) -> None:
    """Verify that all records from a JSON index appear in SQLite after migration."""
    # Create a fake JSON index
    embeddings_dir = tmp_path / "embeddings"
    embeddings_dir.mkdir()

    records = [
        {
            "id": f"rec-{i}",
            "text": f"Test record number {i} with enough text to pass filters",
            "embedding": [0.1 * i] * 768,
            "source": "test",
            "timestamp": f"2026-04-0{i+1}",
            "tags": ["test"],
            "metadata": {},
        }
        for i in range(5)
    ]

    index_path = embeddings_dir / "conversation_index.json"
    index_path.write_text(json.dumps(records), encoding="utf-8")

    # Migrate
    db_path = embeddings_dir / "memory.db"
    store = SqliteVectorStore(db_path)

    for record in records:
        store.insert({
            "id": record["id"],
            "text": record["text"],
            "embedding": record["embedding"],
            "source": record.get("source", ""),
            "memory_type": "conversation",
            "created_at": record.get("timestamp", ""),
            "metadata": record.get("metadata", {}),
        })

    assert store.count() == 5
    store.close()


# ── JSON archival ───────────────────────────────────────────────────────


def test_json_index_archived_not_deleted(tmp_path: Path) -> None:
    """Verify JSON files are moved to archive/, not deleted."""
    import shutil
    from datetime import datetime

    embeddings_dir = tmp_path / "embeddings"
    embeddings_dir.mkdir()
    archive_dir = embeddings_dir / "archive"

    # Create a fake JSON index
    index_path = embeddings_dir / "profile_index.json"
    index_path.write_text("[]", encoding="utf-8")

    # Archive it (same logic as migration script)
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    archive_path = archive_dir / f"profile_index_{timestamp}.json"
    shutil.move(str(index_path), str(archive_path))

    assert not index_path.exists(), "Original JSON should be gone"
    assert archive_path.exists(), "Archive copy should exist"


# ── Profile retrieval after migration ───────────────────────────────────


def test_profile_retrieval_from_sqlite(tmp_path: Path) -> None:
    """Verify profile records are searchable via SQLite after migration."""
    db_path = tmp_path / "memory.db"
    store = SqliteVectorStore(db_path)

    # Insert profile records with a known embedding
    embedding = [0.5] * 768
    store.insert({
        "id": "profile-1",
        "text": "The user works as a Business Systems Analyst in Generative AI",
        "embedding": embedding,
        "source": "api",
        "memory_type": "profile",
        "created_at": "2026-04-03",
        "metadata": {},
    })
    store.insert({
        "id": "profile-2",
        "text": "The user uses they/them and she/her pronouns and is nonbinary",
        "embedding": embedding,
        "source": "api",
        "memory_type": "profile",
        "created_at": "2026-04-03",
        "metadata": {},
    })

    results = store.search(embedding, limit=5, memory_type="profile")

    assert len(results) == 2
    texts = {r["content"] for r in results}
    assert any("Business Systems Analyst" in t for t in texts)
    assert any("pronouns" in t for t in texts)
    store.close()


# ── Write path routing ──────────────────────────────────────────────────


def test_write_memory_routes_conversation_to_sqlite(tmp_path: Path) -> None:
    """Verify write_memory uses SQLite for conversation type."""
    from src.memory.write_memory import SQLITE_MEMORY_TYPES

    assert "conversation" in SQLITE_MEMORY_TYPES
    assert "profile" in SQLITE_MEMORY_TYPES
    assert "reflection" in SQLITE_MEMORY_TYPES
    assert "journal" in SQLITE_MEMORY_TYPES


def test_write_memory_non_migrated_uses_json():
    """Verify non-migrated types still have JSON fallback path."""
    from src.memory.write_memory import SQLITE_MEMORY_TYPES

    # State, task, etc. are not in SQLITE_MEMORY_TYPES
    assert "state" not in SQLITE_MEMORY_TYPES
    assert "task" not in SQLITE_MEMORY_TYPES
    assert "ingested" not in SQLITE_MEMORY_TYPES


# ── Semantic search routing ─────────────────────────────────────────────


def test_semantic_search_sqlite_memory_types():
    """Verify semantic_search knows which types are in SQLite."""
    from src.retrieval.semantic_search import SQLITE_MEMORY_TYPES

    assert "conversation" in SQLITE_MEMORY_TYPES
    assert "profile" in SQLITE_MEMORY_TYPES
    assert "reflection" in SQLITE_MEMORY_TYPES
    assert "journal" in SQLITE_MEMORY_TYPES
    assert "ingested" not in SQLITE_MEMORY_TYPES
