"""
tools/suppress_reflections.py

Suppress junk reflection records by marking them in-place.
Canonical JSON records are NOT deleted - append-only principle.

Adds metadata.quality = "suppressed" and metadata.suppressed_reason
to each flagged record's canonical JSON file. Also hard-deletes the
corresponding row from memory.db (the SQLite vector index) so the
record is invisible to semantic search.

Reflection records are SQLite-backed (memory.db). The legacy JSON index
path (vault/embeddings/reflection_index.json) is dead for reflections,
and any stale file in a user vault is left untouched - same B-RET-001
precedent applied to conversation_index.json.

Known tool-debt: running scripts/rebuild_indexes.py against canonical
records will re-insert suppressed rows into memory.db because the insert
path does not currently read metadata.quality. Re-run this tool after
any manual rebuild. A schema-level "suppressed" propagation would close
this gap but is out of scope.

Usage:
    python tools/audit_reflections.py          # audit first
    python tools/suppress_reflections.py       # then suppress

This is a one-time cleanup tool for junk that accumulated before the
reflection skip filters were tightened.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.config import get_private_vault_path
from src.retrieval.sqlite_vector_store import SqliteVectorStore
from tools.audit_reflections import is_junk_reflection


def suppress_reflections() -> tuple[int, int, str]:
    """
    Mark junk reflections as suppressed in their canonical JSON records
    and hard-delete them from memory.db.

    Returns (total, suppressed_count, summary_text).
    """
    vault = get_private_vault_path()
    reflection_dir = vault / "memory" / "reflection"

    if not reflection_dir.exists():
        return 0, 0, "No reflection directory found."

    lines: list[str] = []
    total = 0
    suppressed = 0
    suppressed_ids: list[str] = []

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    lines.append(f"Reflection Suppression - {timestamp}")
    lines.append(f"{'=' * 50}")

    for json_file in sorted(reflection_dir.glob("*.json")):
        try:
            record = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        # Skip already-suppressed
        metadata = record.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
            record["metadata"] = metadata

        if metadata.get("quality") == "suppressed":
            continue

        text = record.get("text", "")
        total += 1

        junk, reason = is_junk_reflection(text)
        if junk:
            # Flag the canonical record. Append-only: file stays in place.
            metadata["quality"] = "suppressed"
            metadata["suppressed_reason"] = reason
            metadata["suppressed_at"] = timestamp

            json_file.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            record_id = record.get("id")
            if record_id:
                suppressed_ids.append(record_id)

            suppressed += 1
            lines.append(f"  SUPPRESSED: {json_file.name} - {reason}")

    lines.append(f"\nTotal reflections: {total}")
    lines.append(f"Suppressed: {suppressed}")
    lines.append(f"Remaining: {total - suppressed}")

    # Hard-delete suppressed rows from memory.db. Canonical JSON records
    # remain on disk; only the vector index entries are removed so the
    # records are invisible to semantic_search.
    deleted = _delete_from_memory_db(vault, suppressed_ids)
    lines.append(f"memory.db rows deleted: {deleted}")

    summary = "\n".join(lines)
    return total, suppressed, summary


def _delete_from_memory_db(vault: Path, ids: list[str]) -> int:
    """Hard-delete the listed record ids from memory.db.

    Returns the count of rows actually removed. Returns 0 if memory.db
    does not exist (fresh vault, never indexed). Leaves any stale
    reflection_index.json in user vaults untouched - same B-RET-001
    precedent applied to conversation_index.json.
    """
    if not ids:
        return 0

    db_path = vault / "embeddings" / "memory.db"
    if not db_path.exists():
        return 0

    store = SqliteVectorStore(db_path)
    try:
        return store.delete_by_ids(ids)
    finally:
        store.close()


def main():
    print("Running reflection suppression...\n")
    total, suppressed, summary = suppress_reflections()
    print(summary)

    # Write log
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    log_dir = REPO_ROOT / "logs" / "reflection_audit"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"suppression_{timestamp}.log"
    log_file.write_text(summary, encoding="utf-8")
    print(f"\nLog written to: {log_file}")


if __name__ == "__main__":
    main()
