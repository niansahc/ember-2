"""
scripts/rebuild_authorship_reflections.py

Backfill the `authorship` column for existing reflection records in
memory.db (SQLite). Reflections predate live-path authorship classification
(Recalling Too Well Phase 1, item 2) and are stuck at the column default
'unknown', which takes the ranker's conservative 0.5x multiplier on
relational/identity queries.

Precedent: scripts/rebuild_authorship_index.py backfills ingested.db by
source. This script targets memory.db instead, and only
`memory_type = 'reflection'` rows -- conversation/profile/journal backfill
is out of scope for this phase (forward-only for those types; see the
Recalling Too Well Phase 1 plan).

Reuses src/memory/authorship.py::classify_authorship so the live write path
and this backfill can never classify the same record differently.

Index-only migration (CLAUDE.md Rule 3/4): vault JSON records are not
touched. authorship is a derived, rebuildable index fact.

Usage
-----
    python scripts/rebuild_authorship_reflections.py              # dry-run, prints counts
    python scripts/rebuild_authorship_reflections.py --confirm    # actually writes
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory.authorship import classify_authorship  # noqa: E402


def _resolve_db_path() -> Path:
    """Find the memory SQLite index for the active vault."""
    from src.core.config import get_private_vault_path

    return get_private_vault_path() / "embeddings" / "memory.db"


def rebuild(db_path: Path, confirm: bool) -> dict[str, int]:
    """Scan reflection rows and compute authorship labels.

    Returns a {label: count} mapping. When confirm=True, writes the values
    back into the `authorship` column. When confirm=False, this is a pure
    dry-run -- no writes.
    """
    if not db_path.exists():
        print(f"[REBUILD] No SQLite index at {db_path}. Nothing to do.")
        return {}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Ensure the column exists. SqliteVectorStore normally runs this, but
    # this script may be invoked against a vault that hasn't opened the
    # store in the current process.
    try:
        conn.execute(
            "ALTER TABLE vectors ADD COLUMN authorship TEXT DEFAULT 'unknown'"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists

    counts: dict[str, int] = {}
    updates: list[tuple[str, str]] = []

    for row in conn.execute(
        "SELECT id, source, metadata FROM vectors WHERE memory_type = 'reflection'"
    ):
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        label = classify_authorship("reflection", row["source"], metadata)
        counts[label] = counts.get(label, 0) + 1
        updates.append((label, row["id"]))

    if confirm:
        conn.executemany(
            "UPDATE vectors SET authorship = ? WHERE id = ?",
            updates,
        )
        conn.commit()

    conn.close()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Write the computed authorship values. Without this flag, the "
        "script prints counts only (dry-run).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Override the index path. Defaults to the active vault's "
        "embeddings/memory.db.",
    )
    args = parser.parse_args()

    db_path = args.db or _resolve_db_path()
    counts = rebuild(db_path, confirm=args.confirm)
    if not counts:
        return 0

    mode = "WROTE" if args.confirm else "DRY-RUN"
    print(f"[REBUILD] {mode} reflection authorship labels at {db_path}")
    total = sum(counts.values())
    for label in ("first_person", "third_party", "mixed", "unknown"):
        if label in counts:
            pct = counts[label] * 100 / total if total else 0
            print(f"  {label:13s} {counts[label]:>6d}  ({pct:5.1f}%)")
    if not args.confirm:
        print("  Re-run with --confirm to persist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
