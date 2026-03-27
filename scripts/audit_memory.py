"""
scripts/audit_memory.py

Vault health check for Ember-2.

Reads directly from private_vault/ using PRIVATE_VAULT_PATH from
src.core.config. Never writes or modifies any vault files — read-only
inspection only. The only file written is the audit log.

Usage:
    python scripts/audit_memory.py                # full audit, summary only
    python scripts/audit_memory.py --verbose      # full details per check
    python scripts/audit_memory.py --fix          # show what needs manual attention

Checks performed:
    1. Vault inventory — record counts, disk size, date range per type
    2. Schema validation — required fields (id, timestamp, type, text, source, tags, metadata)
    3. Type mismatch — type field doesn't match the folder it lives in
    4. Duplicate detection — identical text within the same memory type
    5. Junk detection — short text, raw JSON, noise markers
    6. Index health — vector index file sizes, record counts, load test
    7. Summary — overall health score with actionable recommendations

Output:
    - stdout: summary (or full detail with --verbose)
    - logs/audit/audit_{timestamp}.log: full detail always
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.config import get_private_vault_path

REQUIRED_FIELDS = ("id", "timestamp", "type", "text", "source", "tags", "metadata")

NOISE_MARKERS = (
    "user asked:",
    "ember responded:",
    "assistant responded:",
    "assistant said:",
    "### task:",
    "generate 1-3 broad tags",
    '"user_message":',
    '"memory_items":',
    '"reflection_items":',
    '"conversation_id":',
    '"chunk_id":',
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _memory_root() -> Path:
    return get_private_vault_path() / "memory"


def _embeddings_root() -> Path:
    return get_private_vault_path() / "embeddings"


def _load_record(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _iter_records(memory_root: Path):
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
    return re.sub(r"\s+", " ", text.strip().lower())


def _format_size(bytes_val: int) -> str:
    if bytes_val >= 1024 * 1024:
        return f"{bytes_val / (1024 * 1024):.2f} MB"
    if bytes_val >= 1024:
        return f"{bytes_val / 1024:.1f} KB"
    return f"{bytes_val} B"


# ---------------------------------------------------------------------------
# Audit checks
# ---------------------------------------------------------------------------

def check_inventory(lines: list[str]) -> dict:
    """Count records per type, disk size, oldest/newest timestamps."""
    lines.append("\n1. VAULT INVENTORY")
    lines.append("=" * 50)

    memory_root = _memory_root()
    stats: dict[str, dict] = {}
    total_records = 0
    total_size = 0

    for type_dir in sorted(memory_root.iterdir()):
        if not type_dir.is_dir():
            continue
        mem_type = type_dir.name
        files = list(type_dir.glob("*.json"))
        count = len(files)
        size = sum(f.stat().st_size for f in files)
        total_records += count
        total_size += size

        timestamps = []
        for f in files:
            rec = _load_record(f)
            if rec and rec.get("timestamp"):
                timestamps.append(rec["timestamp"])

        oldest = min(timestamps) if timestamps else "?"
        newest = max(timestamps) if timestamps else "?"

        stats[mem_type] = {"count": count, "size": size, "oldest": oldest, "newest": newest}

        lines.append(f"  {mem_type:20s}  {count:>6} records  {_format_size(size):>10}  [{oldest[:10]}..{newest[:10]}]")

    lines.append(f"  {'TOTAL':20s}  {total_records:>6} records  {_format_size(total_size):>10}")
    return {"total_records": total_records, "total_size": total_size, "types": stats}


def check_schema(lines: list[str]) -> dict:
    """Check required fields on every record."""
    lines.append("\n2. SCHEMA VALIDATION")
    lines.append("=" * 50)

    memory_root = _memory_root()
    missing_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total_records = 0
    total_missing = 0

    for mem_type, path, record in _iter_records(memory_root):
        total_records += 1
        for field in REQUIRED_FIELDS:
            if field not in record:
                missing_counts[mem_type][field] += 1
                total_missing += 1

    if total_missing == 0:
        lines.append("  All records have all required fields. ✓")
    else:
        for mem_type in sorted(missing_counts):
            for field, count in sorted(missing_counts[mem_type].items()):
                lines.append(f"  [{mem_type}] missing '{field}': {count} records")

    lines.append(f"  Total: {total_missing} missing field(s) across {total_records} records")
    return {"total_missing": total_missing, "total_records": total_records}


def check_type_mismatch(lines: list[str]) -> dict:
    """Check that the type field matches the folder name."""
    lines.append("\n3. TYPE MISMATCH DETECTION")
    lines.append("=" * 50)

    memory_root = _memory_root()
    mismatches = 0

    for mem_type, path, record in _iter_records(memory_root):
        record_type = record.get("type", "")
        # Ingested chunks use "ingested" folder; state records use category as type
        # Allow state category types in the state folder
        if mem_type == "state":
            continue  # State records have category as type, not "state"
        if record_type and record_type != mem_type:
            mismatches += 1
            lines.append(f"  MISMATCH: {path.name} — folder={mem_type}, type={record_type}")

    if mismatches == 0:
        lines.append("  No type mismatches found. ✓")

    lines.append(f"  Total: {mismatches} mismatch(es)")
    return {"mismatches": mismatches}


def check_duplicates(lines: list[str]) -> dict:
    """Find identical text within the same memory type."""
    lines.append("\n4. DUPLICATE DETECTION")
    lines.append("=" * 50)

    memory_root = _memory_root()
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)

    for mem_type, path, record in _iter_records(memory_root):
        text = record.get("text", "")
        if text:
            key = (mem_type, _normalize(str(text)))
            groups[key].append(record.get("id", path.name))

    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}
    total_dupes = sum(len(v) - 1 for v in duplicate_groups.values())

    if not duplicate_groups:
        lines.append("  No duplicates found. ✓")
    else:
        for (mem_type, text_key), ids in sorted(duplicate_groups.items())[:20]:
            preview = text_key[:60] + "..." if len(text_key) > 60 else text_key
            lines.append(f"  [{mem_type}] {len(ids)} copies: \"{preview}\"")
            lines.append(f"    IDs: {', '.join(ids[:5])}{'...' if len(ids) > 5 else ''}")

        if len(duplicate_groups) > 20:
            lines.append(f"  ... and {len(duplicate_groups) - 20} more groups")

    lines.append(f"  Total: {total_dupes} duplicate(s) in {len(duplicate_groups)} group(s)")
    return {"total_dupes": total_dupes, "groups": len(duplicate_groups)}


def check_junk(lines: list[str]) -> dict:
    """Flag junk records."""
    lines.append("\n5. JUNK DETECTION")
    lines.append("=" * 50)

    memory_root = _memory_root()
    junk_by_type: dict[str, int] = defaultdict(int)
    total_junk = 0
    total_records = 0

    for mem_type, path, record in _iter_records(memory_root):
        total_records += 1
        text = record.get("text", "")
        if not isinstance(text, str):
            text = str(text) if text else ""

        stripped = text.strip().lower()
        is_junk = False

        if len(stripped) < 40:
            is_junk = True
        elif stripped.startswith("{") or stripped.startswith("["):
            is_junk = True
        elif "```" in stripped:
            is_junk = True
        elif any(marker in stripped for marker in NOISE_MARKERS):
            is_junk = True

        if is_junk:
            junk_by_type[mem_type] += 1
            total_junk += 1

    if total_junk == 0:
        lines.append("  No junk records found. ✓")
    else:
        for mem_type in sorted(junk_by_type):
            count = junk_by_type[mem_type]
            lines.append(f"  [{mem_type}] {count} junk record(s)")

    junk_pct = (total_junk / total_records * 100) if total_records > 0 else 0
    lines.append(f"  Total: {total_junk} junk record(s) out of {total_records} ({junk_pct:.1f}%)")
    return {"total_junk": total_junk, "total_records": total_records, "junk_pct": junk_pct}


def check_indexes(lines: list[str]) -> dict:
    """Check vector index health."""
    lines.append("\n6. INDEX HEALTH")
    lines.append("=" * 50)

    embeddings_root = _embeddings_root()
    if not embeddings_root.exists():
        lines.append("  No embeddings directory found.")
        return {"indexes": 0}

    index_count = 0
    errors = 0

    for index_file in sorted(embeddings_root.glob("*.json")):
        index_count += 1
        size = index_file.stat().st_size
        size_str = _format_size(size)

        try:
            data = json.loads(index_file.read_text(encoding="utf-8"))
            if isinstance(data, list):
                record_count = len(data)
                lines.append(f"  {index_file.name:35s}  {size_str:>10}  {record_count:>6} records  ✓")
            else:
                lines.append(f"  {index_file.name:35s}  {size_str:>10}  INVALID FORMAT")
                errors += 1
        except (json.JSONDecodeError, OSError) as exc:
            lines.append(f"  {index_file.name:35s}  {size_str:>10}  LOAD ERROR: {exc}")
            errors += 1

    # Check SQLite store
    db_path = embeddings_root / "ingested.db"
    if db_path.exists():
        size_str = _format_size(db_path.stat().st_size)
        lines.append(f"  {'ingested.db':35s}  {size_str:>10}  (SQLite)")

    if index_count == 0:
        lines.append("  No JSON index files found.")

    lines.append(f"  Total: {index_count} index(es), {errors} error(s)")
    return {"indexes": index_count, "errors": errors}


def summary(inventory_result, schema_result, mismatch_result, dupe_result, junk_result, index_result, lines: list[str]) -> str:
    """Generate overall health score."""
    lines.append("\n7. SUMMARY")
    lines.append("=" * 50)

    issues = []
    health = "GREEN"

    # Junk percentage
    junk_pct = junk_result.get("junk_pct", 0)
    if junk_pct > 20:
        health = "RED"
        issues.append(f"High junk rate ({junk_pct:.1f}%) — run tools/suppress_assistant_noise.py")
    elif junk_pct > 5:
        if health == "GREEN":
            health = "YELLOW"
        issues.append(f"Moderate junk rate ({junk_pct:.1f}%) — review flagged records")

    # Missing fields
    missing = schema_result.get("total_missing", 0)
    if missing > 50:
        health = "RED"
        issues.append(f"{missing} missing required fields — ingestion may be writing incomplete records")
    elif missing > 0:
        if health == "GREEN":
            health = "YELLOW"
        issues.append(f"{missing} missing field(s) — mostly ingested chunks (expected)")

    # Type mismatches
    mismatches = mismatch_result.get("mismatches", 0)
    if mismatches > 0:
        if health == "GREEN":
            health = "YELLOW"
        issues.append(f"{mismatches} type mismatch(es) — records in the wrong folder")

    # Index errors
    index_errors = index_result.get("errors", 0)
    if index_errors > 0:
        health = "RED"
        issues.append(f"{index_errors} index load error(s) — rebuild indexes")

    # Duplicates
    dupes = dupe_result.get("total_dupes", 0)
    if dupes > 100:
        if health == "GREEN":
            health = "YELLOW"
        issues.append(f"{dupes} duplicate records — consider dedup pass")

    icon = {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}[health]
    lines.append(f"\n  Vault health: {icon} {health}")
    lines.append(f"  Records: {inventory_result.get('total_records', 0)}")
    lines.append(f"  Size: {_format_size(inventory_result.get('total_size', 0))}")

    if issues:
        lines.append("\n  Recommendations:")
        for issue in issues:
            lines.append(f"    • {issue}")
    else:
        lines.append("\n  No issues found. Vault is healthy.")

    return health


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_audit(verbose: bool = False, fix: bool = False) -> str:
    lines: list[str] = []
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    lines.append(f"Ember-2 Vault Audit — {timestamp}")
    lines.append(f"Vault: {get_private_vault_path()}")
    lines.append("")

    inv = check_inventory(lines)
    schema = check_schema(lines)
    mismatch = check_type_mismatch(lines)
    dupes = check_duplicates(lines)
    junk = check_junk(lines)
    indexes = check_indexes(lines)
    health = summary(inv, schema, mismatch, dupes, junk, indexes, lines)

    if fix:
        lines.append("\n\n--- FIX REPORT ---")
        lines.append("The following items need manual attention:")
        lines.append("(Ember never auto-deletes or modifies records — append-only principle)")
        lines.append("")

        if mismatch["mismatches"] > 0:
            lines.append(f"• {mismatch['mismatches']} record(s) have type field != folder name")
            lines.append("  Action: manually review and re-ingest with correct type")

        if junk["total_junk"] > 0:
            lines.append(f"• {junk['total_junk']} junk record(s) detected")
            lines.append("  Action: run tools/suppress_assistant_noise.py to flag in index")

        if indexes.get("errors", 0) > 0:
            lines.append(f"• {indexes['errors']} vector index(es) failed to load")
            lines.append("  Action: delete corrupt index files and rebuild with embed_memory")

        if dupes["total_dupes"] > 50:
            lines.append(f"• {dupes['total_dupes']} duplicate records")
            lines.append("  Action: review for ingestion bugs, duplicates are low-priority")

        if schema["total_missing"] > 0:
            lines.append(f"• {schema['total_missing']} missing required field(s)")
            lines.append("  Action: mostly ingested chunks missing 'type' field (pre-enforcement)")

    full_output = "\n".join(lines)
    return full_output


def main():
    verbose = "--verbose" in sys.argv
    fix = "--fix" in sys.argv

    output = run_audit(verbose=verbose, fix=fix)

    # Print to stdout
    print(output)

    # Write to log file
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    log_dir = REPO_ROOT / "logs" / "audit"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"audit_{timestamp}.log"
    log_file.write_text(output, encoding="utf-8")
    print(f"\nLog written to: {log_file}")


if __name__ == "__main__":
    main()
