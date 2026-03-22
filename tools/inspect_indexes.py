"""
tools/inspect_indexes.py

Diagnostic: inspect what's in each vector index file.

Prints for each index:
  - record count
  - first 3 records: id, memory_type, 100-char content preview

Usage
-----
python tools/inspect_indexes.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.config import get_private_vault_path


def inspect_indexes() -> None:
    vault = get_private_vault_path()
    embeddings_dir = vault / "embeddings"

    if not embeddings_dir.exists():
        print(f"Embeddings directory not found: {embeddings_dir}")
        return

    index_files = sorted(embeddings_dir.glob("*_index.json"))

    if not index_files:
        print("No *_index.json files found.")
        return

    for index_path in index_files:
        size_mb = index_path.stat().st_size / (1024 * 1024)
        print(f"{'=' * 60}")
        print(f"  {index_path.name}  ({size_mb:.2f} MB)")
        print(f"{'=' * 60}")

        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  ERROR reading file: {exc}\n")
            continue

        if not isinstance(data, list):
            print(f"  Unexpected format: {type(data)}\n")
            continue

        print(f"  Records: {len(data)}")
        print()

        for i, record in enumerate(data[:3]):
            rec_id = record.get("id") or record.get("file_path") or record.get("chunk_id") or "—"
            mem_type = record.get("memory_type") or record.get("type") or "—"
            text = record.get("text") or record.get("content") or ""
            preview = text[:100].replace("\n", " ").strip()
            has_embedding = "embedding" in record and bool(record["embedding"])
            print(f"  [{i + 1}] id:          {rec_id}")
            print(f"       memory_type: {mem_type}")
            print(f"       embedding:   {'yes (' + str(len(record['embedding'])) + 'd)' if has_embedding else 'missing'}")
            print(f"       preview:     {preview}")
            print()

        print()


if __name__ == "__main__":
    inspect_indexes()
