"""
scripts/rebuild_authorship_index.py

Populate the SQLite ingested-index `authorship` column for existing records.

Index-only migration per user decision ① on task #24 / cluster 8. Vault JSON
records are not modified (append-only rule, CLAUDE.md §3). The authorship
value is a derived fact, rebuildable at any time from source + role signals.

Rules
-----
- source == "chatgpt_export", text body begins with "user:" → first_person
- source == "chatgpt_export", text body begins with "assistant:" -> mixed
  (was third_party until #218; ADR-015 governs -- the imported assistant
  side is Ember's own continuity, not third-party material)
- source == "obsidian_export" → first_person
- source == "journal" → first_person
- source in {"pdf", "docx", "book", "epub", "html", "article"} -> unknown
  (genuine third-party material is owned by the `ingested` memory type
  and ADR-018 type gating, not by an authorship multiplier -- #218)
- anything else → unknown (the column default)

ChatGPT role detection is text-prefix based for now. The dedicated normalizer
(task #25) moves role into metadata.role and lets this script use a cleaner
lookup — until that ships, the prefix rule is the most reliable signal.

Usage
-----
    python scripts/rebuild_authorship_index.py              # dry-run, prints counts
    python scripts/rebuild_authorship_index.py --confirm    # actually writes
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


# Source → authorship mapping. "chatgpt_export" is handled specially (role-
# dependent), so it's not in this table.
_SOURCE_AUTHORSHIP: dict[str, str] = {
    "obsidian_export": "first_person",
    "journal": "first_person",
    "pdf": "unknown",
    "docx": "unknown",
    "book": "unknown",
    "epub": "unknown",
    "html": "unknown",
    "article": "unknown",
}


def _classify(source: str | None, text: str | None, metadata_str: str | None) -> str:
    """Derive an authorship label for a single record.

    The lookup order is deliberate: explicit source mapping first, then
    ChatGPT text-prefix heuristic, then unknown. A future task #25 can
    short-circuit the ChatGPT branch by reading metadata.role directly.
    """
    src = (source or "").lower()
    mapped = _SOURCE_AUTHORSHIP.get(src)
    if mapped:
        return mapped

    if src == "chatgpt_export":
        body = (text or "").lstrip().lower()
        if body.startswith("user:"):
            return "first_person"
        if body.startswith("assistant:"):
            # #218: mixed, not third_party. ADR-015 holds that the imported
            # assistant side is part of Ember's own continuity rather than
            # third-party material, and classify_authorship already returns
            # mixed for the same record on the live path.
            return "mixed"

        # metadata.role fallback — task #25 landed: the importer now sets
        # metadata.roles per-message and the chunker propagates to
        # metadata.role. The text-prefix branch above is retained as a
        # safety net for pre-normalization records only.
        try:
            metadata = json.loads(metadata_str or "{}")
            role = (metadata.get("role") or "").lower()
            if role == "user":
                return "first_person"
            if role == "assistant":
                return "mixed"  # #218, as above
        except (json.JSONDecodeError, TypeError):
            pass

    return "unknown"


def _resolve_db_path() -> Path:
    """Find the ingested SQLite index for the active vault."""
    # Lazy import so the script can run without a full app init.
    from src.core.config import get_private_vault_path

    return get_private_vault_path() / "embeddings" / "ingested.db"


def rebuild(db_path: Path, confirm: bool) -> dict[str, int]:
    """Scan the index and compute authorship labels.

    Returns a {label: count} mapping. When confirm=True, writes the values
    back into the `authorship` column. When confirm=False, this is a pure
    dry-run — no writes.
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
        pass

    counts: dict[str, int] = {}
    updates: list[tuple[str, str]] = []

    for row in conn.execute("SELECT id, source, text, metadata FROM vectors"):
        label = _classify(row["source"], row["text"], row["metadata"])
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
        "embeddings/ingested.db.",
    )
    args = parser.parse_args()

    db_path = args.db or _resolve_db_path()
    counts = rebuild(db_path, confirm=args.confirm)
    if not counts:
        return 0

    mode = "WROTE" if args.confirm else "DRY-RUN"
    print(f"[REBUILD] {mode} authorship labels at {db_path}")
    total = sum(counts.values())
    for label in ("first_person", "mixed", "unknown"):
        if label in counts:
            pct = counts[label] * 100 / total if total else 0
            print(f"  {label:13s} {counts[label]:>6d}  ({pct:5.1f}%)")
    if not args.confirm:
        print("  Re-run with --confirm to persist.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
