"""
scripts/rebuild_indexes.py

Full index rebuild for Ember-2.

Rebuilds all vector indexes from canonical vault records using the
current embedding model. Run this after changing the embedding model
(e.g. switching from sentence-transformers to nomic-embed-text).

Indexes are derived artifacts — they can always be deleted and rebuilt
from the canonical JSON records in private_vault/memory/. This script
is the rebuild mechanism.

IMPORTANT: Stop the API before running this script. The API caches
indexes in memory, so running a rebuild while the API is live will
cause stale cache issues.

Usage:
    python scripts/rebuild_indexes.py              # rebuild all
    python scripts/rebuild_indexes.py --type conversation  # one type only
    python scripts/rebuild_indexes.py --skip-sqlite        # JSON indexes only

Progress is printed every batch so it doesn't look hung on large corpora.
"""

from __future__ import annotations

import json
import sqlite3
import struct
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.config import get_private_vault_path
from src.retrieval.embedding_model import embed_texts

# Batch size for embedding calls — balances speed vs memory
BATCH_SIZE = 50

# Memory types that use JSON indexes.
# B-RET-001 retired "conversation" first. The follow-up cleanup retired
# profile, reflection, and journal: src/memory/write_memory.py and
# src/retrieval/semantic_search.py both route those types through
# SQLITE_MEMORY_TYPES, and no live retrieval path reads the per-type
# JSON indexes. The list is intentionally empty - any future memory
# type that uses a JSON index would be added here, but none currently
# do. Stale {type}_index.json files in user vaults are left in place
# (user data); nothing live reads them.
JSON_INDEX_TYPES = []


def rebuild_json_index(vault: Path, memory_type: str) -> int:
    """
    Rebuild a single JSON vector index from canonical vault records.

    Reads all .json files in vault/memory/{memory_type}/, embeds the
    text field from each, and writes a new index file.

    Returns the number of records indexed.
    """
    memory_dir = vault / "memory" / memory_type
    index_path = vault / "embeddings" / f"{memory_type}_index.json"

    if not memory_dir.exists():
        print(f"  [{memory_type}] No memory directory found — skipping")
        return 0

    # Collect all canonical records
    files = sorted(memory_dir.glob("*.json"))
    if not files:
        print(f"  [{memory_type}] No records found — writing empty index")
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("[]", encoding="utf-8")
        return 0

    print(f"  [{memory_type}] {len(files)} canonical records to index")

    records = []
    texts = []
    skipped = 0

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            text = data.get("text", "")
            if not text or not text.strip():
                skipped += 1
                continue
            records.append((f, data))
            texts.append(text)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [{memory_type}] Error reading {f.name}: {e}")
            skipped += 1

    if not texts:
        print(f"  [{memory_type}] No valid texts — writing empty index")
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("[]", encoding="utf-8")
        return 0

    # Embed in batches
    all_embeddings = []
    total = len(texts)
    for i in range(0, total, BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        embeddings = embed_texts(batch)
        all_embeddings.extend(embeddings)
        done = min(i + BATCH_SIZE, total)
        pct = done / total * 100
        print(f"  [{memory_type}] {done}/{total} embedded ({pct:.0f}%)")

    # Build index entries
    index_data = []
    for (f, data), embedding in zip(records, all_embeddings):
        text = data.get("text", "")
        normalized = text.lower().strip()
        import re
        normalized = re.sub(r"\s+", " ", normalized)

        index_data.append({
            "id": data.get("id", f.stem),
            "timestamp": data.get("timestamp", ""),
            "type": data.get("type", memory_type),
            "text": text,
            "normalized_text": normalized,
            "source": data.get("source", ""),
            "tags": data.get("tags", []),
            "file_path": str(f),
            "embedding": embedding,
            "metadata": data.get("metadata", {}),
        })

    # Write index
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("w", encoding="utf-8") as fh:
        json.dump(index_data, fh, ensure_ascii=False)

    size_mb = index_path.stat().st_size / (1024 * 1024)
    if skipped:
        print(f"  [{memory_type}] Done: {len(index_data)} indexed, {skipped} skipped ({size_mb:.1f} MB)")
    else:
        print(f"  [{memory_type}] Done: {len(index_data)} indexed ({size_mb:.1f} MB)")

    return len(index_data)


def rebuild_sqlite_index(vault: Path) -> int:
    """
    Rebuild embeddings in the ingested SQLite vector store.

    Reads each row's text, re-embeds it, and updates the embedding BLOB
    in place. Does not change any other fields.

    Returns the number of records updated.
    """
    db_path = vault / "embeddings" / "ingested.db"
    if not db_path.exists():
        print("  [ingested] No ingested.db found — skipping")
        return 0

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    print(f"  [ingested] {total} records to re-embed")

    if total == 0:
        conn.close()
        return 0

    # Read all ids and texts
    cursor = conn.execute("SELECT id, text FROM vectors")
    rows = cursor.fetchall()

    updated = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        texts = [row["text"] for row in batch]
        ids = [row["id"] for row in batch]

        embeddings = embed_texts(texts)

        for record_id, embedding in zip(ids, embeddings):
            n = len(embedding)
            blob = struct.pack(f"{n}f", *embedding)
            conn.execute(
                "UPDATE vectors SET embedding = ? WHERE id = ?",
                (blob, record_id),
            )

        conn.commit()
        updated += len(batch)
        done = min(i + BATCH_SIZE, len(rows))
        pct = done / len(rows) * 100
        print(f"  [ingested] {done}/{len(rows)} re-embedded ({pct:.0f}%)")

    conn.close()
    print(f"  [ingested] Done: {updated} records updated")
    return updated


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    import argparse
    parser = argparse.ArgumentParser(description="Rebuild Ember-2 vector indexes")
    parser.add_argument("--type", help="Rebuild only this memory type")
    parser.add_argument("--skip-sqlite", action="store_true", help="Skip SQLite ingested index")
    args = parser.parse_args()

    vault = get_private_vault_path()
    print(f"Vault: {vault}")
    print(f"Embedding model: {__import__('src.core.config', fromlist=['get_ember_embed_model']).get_ember_embed_model()}")
    print()

    start = time.time()
    total_records = 0

    # Rebuild JSON indexes
    types_to_rebuild = [args.type] if args.type else JSON_INDEX_TYPES
    for memory_type in types_to_rebuild:
        if memory_type == "ingested":
            continue  # handled by SQLite
        total_records += rebuild_json_index(vault, memory_type)

    # Rebuild SQLite index
    if not args.skip_sqlite and args.type in (None, "ingested"):
        print()
        total_records += rebuild_sqlite_index(vault)

    elapsed = time.time() - start
    print()
    print(f"{'=' * 50}")
    print(f"  Rebuild complete")
    print(f"  Total records: {total_records}")
    print(f"  Time: {elapsed:.1f}s ({elapsed/60:.1f} minutes)")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
