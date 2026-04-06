"""
scripts/migrate_json_to_sqlite.py

Migrate conversation, profile, reflection, and journal vector indexes
from JSON files to a single SQLite database (memory.db).

IMPORTANT: Stop the API before running this script. The API caches
indexes in memory — running a migration while the API is live will
cause stale cache issues.

Indexes are derived artifacts — they can always be deleted and rebuilt
from the canonical JSON records in private_vault/memory/. This script
migrates the existing JSON indexes to SQLite for better performance
and consistency with the ingested.db pattern.

Old JSON index files are archived to private_vault/embeddings/archive/,
not deleted. The migration is safe to re-run (INSERT OR IGNORE).

Usage:
    python scripts/migrate_json_to_sqlite.py
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.config import get_private_vault_path
from src.retrieval.sqlite_vector_store import SqliteVectorStore

# Memory types to migrate from JSON to SQLite
MIGRATE_TYPES = ["conversation", "profile", "reflection", "journal"]


def migrate_index(store: SqliteVectorStore, vault: Path, memory_type: str) -> int:
    """
    Migrate a single JSON vector index into the SQLite store.

    Returns the number of records inserted.
    """
    index_path = vault / "embeddings" / f"{memory_type}_index.json"

    if not index_path.exists():
        print(f"  [{memory_type}] No JSON index found — skipping")
        return 0

    size_mb = index_path.stat().st_size / (1024 * 1024)
    print(f"  [{memory_type}] Reading {index_path.name} ({size_mb:.1f} MB)")

    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"  [{memory_type}] Error reading JSON: {e}")
        return 0

    if not isinstance(data, list):
        print(f"  [{memory_type}] Expected list, got {type(data)} — skipping")
        return 0

    print(f"  [{memory_type}] {len(data)} records to migrate")

    inserted = 0
    skipped = 0

    for record in data:
        embedding = record.get("embedding")
        text = record.get("text", "")
        record_id = record.get("id") or record.get("file_path", "")

        if not embedding or not text or not record_id:
            skipped += 1
            continue

        metadata = record.get("metadata", {})
        if record.get("file_path"):
            metadata["file_path"] = record["file_path"]
        if record.get("tags"):
            metadata["tags"] = record["tags"]
        if record.get("normalized_text"):
            metadata["normalized_text"] = record["normalized_text"]
        if record.get("source"):
            metadata["source_field"] = record["source"]

        store.insert({
            "id": record_id,
            "text": text,
            "embedding": embedding,
            "source": record.get("source", ""),
            "memory_type": memory_type,
            "created_at": record.get("timestamp", ""),
            "metadata": metadata,
        })
        inserted += 1

    print(f"  [{memory_type}] Migrated: {inserted} inserted, {skipped} skipped")
    return inserted


def archive_json_index(vault: Path, memory_type: str) -> None:
    """Archive a JSON index file to embeddings/archive/."""
    index_path = vault / "embeddings" / f"{memory_type}_index.json"
    if not index_path.exists():
        return

    archive_dir = vault / "embeddings" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    archive_name = f"{memory_type}_index_{timestamp}.json"
    archive_path = archive_dir / archive_name

    shutil.move(str(index_path), str(archive_path))
    print(f"  [{memory_type}] Archived to {archive_path.name}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    vault = get_private_vault_path()
    db_path = vault / "embeddings" / "memory.db"

    print(f"Vault: {vault}")
    print(f"Target: {db_path}")
    print()

    store = SqliteVectorStore(db_path)
    total = 0

    for memory_type in MIGRATE_TYPES:
        count = migrate_index(store, vault, memory_type)
        total += count

    print()

    # Archive old JSON files
    print("Archiving JSON index files...")
    for memory_type in MIGRATE_TYPES:
        archive_json_index(vault, memory_type)

    final_count = store.count()
    store.close()

    print()
    print("=" * 50)
    print(f"  Migration complete")
    print(f"  Records migrated: {total}")
    print(f"  DB total count:   {final_count}")
    print(f"  DB path:          {db_path}")
    print("=" * 50)


if __name__ == "__main__":
    main()
