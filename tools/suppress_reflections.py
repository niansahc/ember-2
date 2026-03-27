"""
tools/suppress_reflections.py

Suppress junk reflection records by marking them in-place.
Does NOT delete records — append-only principle.

Adds metadata.quality = "suppressed" and metadata.suppressed_reason
to each flagged record's JSON file. Also updates the reflection vector
index to exclude suppressed records.

Usage:
    python tools/audit_reflections.py          # audit first
    python tools/suppress_reflections.py       # then suppress

This is a one-time cleanup tool for junk that accumulated before
the reflection skip filters were tightened.
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
from tools.audit_reflections import is_junk_reflection


def suppress_reflections() -> tuple[int, int, str]:
    """
    Mark junk reflections as suppressed in their JSON files.
    Returns (total, suppressed_count, summary_text).
    """
    vault = get_private_vault_path()
    reflection_dir = vault / "memory" / "reflection"

    if not reflection_dir.exists():
        return 0, 0, "No reflection directory found."

    lines: list[str] = []
    total = 0
    suppressed = 0

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    lines.append(f"Reflection Suppression — {timestamp}")
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
            # Mark as suppressed in the record itself
            metadata["quality"] = "suppressed"
            metadata["suppressed_reason"] = reason
            metadata["suppressed_at"] = timestamp

            json_file.write_text(
                json.dumps(record, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            suppressed += 1
            lines.append(f"  SUPPRESSED: {json_file.name} — {reason}")

    lines.append(f"\nTotal reflections: {total}")
    lines.append(f"Suppressed: {suppressed}")
    lines.append(f"Remaining: {total - suppressed}")

    # Update the reflection vector index to exclude suppressed records
    index_suppressed = _update_reflection_index(vault, reflection_dir)
    lines.append(f"Index entries removed: {index_suppressed}")

    summary = "\n".join(lines)
    return total, suppressed, summary


def _update_reflection_index(vault: Path, reflection_dir: Path) -> int:
    """Remove suppressed records from the reflection vector index."""
    index_path = vault / "embeddings" / "reflection_index.json"
    if not index_path.exists():
        return 0

    try:
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return 0

    if not isinstance(index_data, list):
        return 0

    # Build set of suppressed file paths
    suppressed_paths = set()
    for json_file in reflection_dir.glob("*.json"):
        try:
            record = json.loads(json_file.read_text(encoding="utf-8"))
            metadata = record.get("metadata", {})
            if isinstance(metadata, dict) and metadata.get("quality") == "suppressed":
                suppressed_paths.add(str(json_file))
        except (json.JSONDecodeError, OSError):
            continue

    # Filter index
    original_count = len(index_data)
    filtered = [
        item for item in index_data
        if item.get("file_path") not in suppressed_paths
    ]

    removed = original_count - len(filtered)

    if removed > 0:
        index_path.write_text(
            json.dumps(filtered, ensure_ascii=False),
            encoding="utf-8",
        )

    return removed


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
