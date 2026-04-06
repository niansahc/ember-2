"""
scripts/migrate_to_sqlite.py

One-time migration script: reads private_vault/embeddings/ingested_index.json
and writes all records into a SqliteVectorStore at
private_vault/embeddings/ingested.db.

Background
----------
ingested_index.json grew to ~1.32 GB (16,728 records). The VectorIndex
50 MB size guard silently skips it on every search, meaning ingested
content has been excluded from semantic retrieval. This migration moves
the index to SQLite, which stores embeddings as compact binary BLOBs
(~47 MB for the same data) and supports row-level inserts and queries.

After running this script and verifying the record count, the original
ingested_index.json can be deleted manually. This script never deletes it.

Usage
-----
python scripts/migrate_to_sqlite.py

The script is safe to re-run. SqliteVectorStore uses INSERT OR IGNORE,
so already-migrated records are skipped without error.

Streaming
---------
If the ijson package is installed, records are streamed from the JSON
file one at a time — avoiding loading 1.32 GB into memory at once.
If ijson is not available, the script falls back to json.loads (which
requires the full file in memory, ~6–8 GB resident during parse on
a 1.32 GB file). Install ijson for best results:

    pip install ijson
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.config import get_private_vault_path
from src.retrieval.sqlite_vector_store import SqliteVectorStore

PROGRESS_INTERVAL = 1000


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------

def _iter_records_ijson(json_path: Path):
    """
    Stream records from a JSON array file using ijson.

    Yields one dict at a time without loading the full file into memory.
    Requires: pip install ijson
    """
    import ijson  # noqa: F401 — checked by caller

    with json_path.open("rb") as f:
        yield from ijson.items(f, "item")


def _iter_records_stdlib(json_path: Path):
    """
    Load the full JSON array and iterate records using the stdlib.

    Falls back to this when ijson is not installed. Requires loading the
    entire file into memory — may use 6–8 GB of RAM for a 1.32 GB file.
    """
    print("[MIGRATE] ijson not available — loading full JSON into memory (may be slow)")
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected a JSON array in {json_path}, got {type(data)}")
    yield from data


def _iter_records(json_path: Path):
    """
    Yield records from the JSON index, streaming if ijson is available.
    """
    try:
        import ijson  # noqa: F401
        print("[MIGRATE] ijson available — streaming records from JSON")
        yield from _iter_records_ijson(json_path)
    except ImportError:
        yield from _iter_records_stdlib(json_path)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_record(record: dict, index: int) -> bool:
    """
    Return True if the record has the required fields for migration.

    ingested_index.json entries use file_path as the unique identifier
    (not 'id'). Required fields are: file_path, text, embedding.

    Emits a warning and returns False if any required field is missing or
    if the embedding is not a non-empty list.
    """
    missing = [f for f in ("file_path", "text", "embedding") if f not in record]
    if missing:
        warnings.warn(
            f"Record at index {index} missing fields {missing} — skipping"
        )
        return False

    embedding = record.get("embedding")
    if not isinstance(embedding, list) or len(embedding) == 0:
        warnings.warn(
            f"Record at index {index} (file_path={record.get('file_path')!r}) has "
            f"invalid embedding — skipping"
        )
        return False

    return True


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def migrate() -> None:
    """
    Read ingested_index.json and write all valid records to ingested.db.

    Prints progress every 1000 records and a final summary.
    Never deletes the source JSON file.
    """
    vault = get_private_vault_path()
    source_path = vault / "embeddings" / "ingested_index.json"
    db_path = vault / "embeddings" / "ingested.db"

    if not source_path.exists():
        print(f"[MIGRATE] Source file not found: {source_path}")
        print("[MIGRATE] Nothing to migrate.")
        return

    source_size_mb = source_path.stat().st_size / (1024 * 1024)
    print(f"[MIGRATE] Source: {source_path} ({source_size_mb:.1f} MB)")
    print(f"[MIGRATE] Target: {db_path}")
    print()

    store = SqliteVectorStore(db_path)

    total = 0
    inserted = 0
    skipped = 0

    for record in _iter_records(source_path):
        if not _validate_record(record, total):
            skipped += 1
            total += 1
            continue

        # ingested_index.json entries use file_path as the unique identifier.
        # Pull source, created_at, and memory_type from metadata if not top-level.
        metadata = record.get("metadata") or {}
        record_id = record["file_path"]

        store.insert(
            {
                "id": record_id,
                "text": record["text"],
                "embedding": record["embedding"],
                "source": record.get("source") or metadata.get("source"),
                "memory_type": record.get("memory_type") or metadata.get("type", "ingested"),
                "created_at": record.get("created_at") or metadata.get("created_at"),
                "metadata": metadata,
            }
        )
        inserted += 1
        total += 1

        if total % PROGRESS_INTERVAL == 0:
            print(f"[MIGRATE] {total} records processed ({inserted} inserted, {skipped} skipped)...")

    final_count = store.count()
    store.close()

    print()
    print("=" * 50)
    print(f"  Migration complete")
    print(f"  Records processed : {total}")
    print(f"  Records inserted  : {inserted}")
    print(f"  Records skipped   : {skipped}")
    print(f"  DB total count    : {final_count}")
    print(f"  DB path           : {db_path}")
    print("=" * 50)
    print()
    print("[MIGRATE] The original ingested_index.json has NOT been deleted.")
    print("[MIGRATE] Verify the DB count above, then delete it manually when ready.")


if __name__ == "__main__":
    migrate()
