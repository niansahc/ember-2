"""
scripts/audit_memory.py

CLI tool for auditing the Ember-2 private vault.

Reads directly from private_vault/memory/ subdirectories using
PRIVATE_VAULT_PATH from src.core.config. Never writes or modifies
any vault files — read-only inspection only.

Usage
-----
python scripts/audit_memory.py inventory
python scripts/audit_memory.py duplicates
python scripts/audit_memory.py junk
python scripts/audit_memory.py missing-fields
python scripts/audit_memory.py all
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import warnings
from collections import defaultdict
from pathlib import Path

# Allow running from repo root without installing the package.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.config import get_private_vault_path

REQUIRED_FIELDS = ("id", "timestamp", "type", "text", "source")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _memory_root() -> Path:
    """Return the memory/ directory inside the private vault."""
    return get_private_vault_path() / "memory"


def _load_record(path: Path) -> dict | None:
    """
    Load a single JSON record from disk.

    Returns None and emits a warning if the file cannot be parsed.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        warnings.warn(f"Skipping unreadable file {path}: {exc}")
        return None


def _iter_records(memory_root: Path):
    """
    Yield (memory_type, path, record_dict) for every JSON file under
    memory_root, skipping any subdirectory that does not exist.

    Unreadable files are skipped with a warning.
    """
    if not memory_root.exists():
        return

    for type_dir in sorted(memory_root.iterdir()):
        if not type_dir.is_dir():
            continue
        memory_type = type_dir.name
        for json_file in sorted(type_dir.glob("*.json")):
            record = _load_record(json_file)
            if record is not None:
                yield memory_type, json_file, record


def _normalize(text: str) -> str:
    """Lowercase and collapse all whitespace to a single space."""
    return re.sub(r"\s+", " ", text.strip().lower())


# ---------------------------------------------------------------------------
# Audit functions
# ---------------------------------------------------------------------------

def inventory() -> int:
    """
    Count records per memory type directory and print a summary table.

    Returns the total record count.
    """
    print("\nMemory Inventory")
    print("----------------")

    memory_root = _memory_root()
    counts: dict[str, int] = defaultdict(int)

    for memory_type, _path, _record in _iter_records(memory_root):
        counts[memory_type] += 1

    if not counts:
        print("  (no records found)")
        print(f"\nTOTAL:           0 records")
        return 0

    # Align counts to the longest type name.
    max_name_len = max(len(name) for name in counts)
    total = 0

    for memory_type in sorted(counts):
        count = counts[memory_type]
        total += count
        label = f"{memory_type}:".ljust(max_name_len + 2)
        print(f"  {label} {count:>6} records")

    separator_len = max_name_len + 2
    print(f"  {'TOTAL:'.ljust(separator_len)} {total:>6} records")

    return total


def find_duplicates() -> int:
    """
    Find records with identical normalized text within the same memory type.

    Prints each duplicate group with file paths.
    Returns the total number of duplicate records found (original excluded).
    """
    print("\nDuplicate Records")
    print("-----------------")

    memory_root = _memory_root()

    # Group file paths by (memory_type, normalized_text).
    groups: dict[tuple[str, str], list[Path]] = defaultdict(list)

    for memory_type, path, record in _iter_records(memory_root):
        text = record.get("text", "")
        key = (memory_type, _normalize(str(text)))
        groups[key].append(path)

    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}

    if not duplicate_groups:
        print("  No duplicates found.")
        print("\nSummary: 0 duplicate records")
        return 0

    total_duplicates = 0

    for (memory_type, normalized_text), paths in sorted(duplicate_groups.items()):
        extra_count = len(paths) - 1
        total_duplicates += extra_count
        preview = normalized_text[:80] + ("…" if len(normalized_text) > 80 else "")
        print(f"\n  [{memory_type}] \"{preview}\" ({len(paths)} copies)")
        for path in paths:
            print(f"    {path}")

    print(f"\nSummary: {total_duplicates} duplicate record(s) across {len(duplicate_groups)} group(s)")
    return total_duplicates


def find_junk() -> int:
    """
    Flag records that look like junk based on heuristic checks:
      - text under 30 characters
      - text starting with { or [  (raw JSON leaked into text field)
      - text containing structural keywords: chunk_id, embedding, memory_items
      - text that is pure whitespace

    Prints flagged records with file path and reason.
    Returns the count of flagged records.
    """
    print("\nJunk Records")
    print("------------")

    memory_root = _memory_root()
    flagged = 0

    junk_keywords = ("chunk_id", "embedding", "memory_items")

    for memory_type, path, record in _iter_records(memory_root):
        text = record.get("text", "")
        reasons: list[str] = []

        if not isinstance(text, str):
            reasons.append("text field is not a string")
        else:
            stripped = text.strip()

            if not stripped:
                reasons.append("text is empty or pure whitespace")
            elif len(stripped) < 30:
                reasons.append(f"text too short ({len(stripped)} chars)")

            if stripped.startswith("{") or stripped.startswith("["):
                reasons.append("text starts with { or [ (possible raw JSON)")

            for keyword in junk_keywords:
                if keyword in stripped:
                    reasons.append(f"text contains structural keyword '{keyword}'")
                    break

        if reasons:
            flagged += 1
            print(f"\n  [{memory_type}] {path.name}")
            print(f"    Path: {path}")
            for reason in reasons:
                print(f"    Reason: {reason}")

    if flagged == 0:
        print("  No junk records found.")

    print(f"\nSummary: {flagged} junk record(s) flagged")
    return flagged


def find_missing_fields() -> int:
    """
    Flag records missing one or more required fields:
    id, timestamp, type, text, source.

    Prints file path and which fields are absent.
    Returns the count of records with missing fields.
    """
    print("\nRecords With Missing Fields")
    print("---------------------------")

    memory_root = _memory_root()
    flagged = 0

    for memory_type, path, record in _iter_records(memory_root):
        missing = [field for field in REQUIRED_FIELDS if field not in record]

        if missing:
            flagged += 1
            print(f"\n  [{memory_type}] {path.name}")
            print(f"    Path: {path}")
            print(f"    Missing: {', '.join(missing)}")

    if flagged == 0:
        print("  No records with missing fields.")

    print(f"\nSummary: {flagged} record(s) with missing fields")
    return flagged


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit the Ember-2 private vault for inventory, duplicates, junk, and schema issues.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/audit_memory.py inventory\n"
            "  python scripts/audit_memory.py duplicates\n"
            "  python scripts/audit_memory.py junk\n"
            "  python scripts/audit_memory.py missing-fields\n"
            "  python scripts/audit_memory.py all\n"
        ),
    )

    parser.add_argument(
        "command",
        choices=["inventory", "duplicates", "junk", "missing-fields", "all"],
        help="Audit to run. Use 'all' to run every check in sequence.",
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if args.command == "inventory":
        inventory()
    elif args.command == "duplicates":
        find_duplicates()
    elif args.command == "junk":
        find_junk()
    elif args.command == "missing-fields":
        find_missing_fields()
    elif args.command == "all":
        inventory()
        find_duplicates()
        find_junk()
        find_missing_fields()


if __name__ == "__main__":
    main()
