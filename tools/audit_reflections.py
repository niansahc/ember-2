"""
tools/audit_reflections.py

Read-only diagnostic of the reflection corpus.
Flags junk records that match known assistant filler patterns.

Usage:
    python tools/audit_reflections.py
    python tools/audit_reflections.py --verbose

Output:
    - stdout: summary + flagged records
    - logs/reflection_audit/audit_{timestamp}.log
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.config import get_private_vault_path
from src.memory.storage import MemoryStorage

storage = MemoryStorage()

JUNK_MARKERS = (
    "there is no earlier conversation",
    "no conversation summary",
    "i understand you're seeking",
    "i understand you are seeking",
    "seeking clarity",
    "what would you like to discuss",
    "i'm here to support",
    "i'm here to help",
    "how can i assist",
    "let me clarify",
)


def is_junk_reflection(text: str) -> tuple[bool, str]:
    """Check if a reflection record is junk. Returns (is_junk, reason)."""
    lower = text.lower().strip()

    if not lower:
        return True, "empty"

    if len(lower) < 100:
        return True, f"too short ({len(lower)} chars)"

    if lower.startswith("{") or lower.startswith("["):
        return True, "JSON artifact"

    if "```" in lower:
        return True, "contains code block"

    for marker in JUNK_MARKERS:
        if marker in lower:
            return True, f"assistant filler: '{marker}'"

    # Assistant voice: too many I-sentences
    sentences = re.split(r"[.!?]\s+", lower)
    i_sentences = sum(1 for s in sentences if s.strip().startswith("i "))
    if len(sentences) > 2 and i_sentences > 3 and i_sentences / len(sentences) > 0.5:
        return True, f"assistant voice ({i_sentences}/{len(sentences)} I-sentences)"

    return False, ""


def run_audit(verbose: bool = False) -> tuple[list[dict], str]:
    vault = get_private_vault_path()
    reflection_dir = vault / "memory" / "reflection"

    if not reflection_dir.exists():
        return [], "No reflection directory found."

    lines: list[str] = []
    flagged: list[dict] = []
    total = 0

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    lines.append(f"Reflection Quality Audit — {timestamp}")
    lines.append(f"{'=' * 50}")

    for json_file in sorted(reflection_dir.glob("*.json")):
        try:
            record = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        # Skip already-suppressed records
        metadata = record.get("metadata", {})
        if isinstance(metadata, dict) and metadata.get("quality") == "suppressed":
            continue

        text = record.get("text", "")
        total += 1

        junk, reason = is_junk_reflection(text)
        if junk:
            flagged.append({
                "file": json_file.name,
                "path": str(json_file),
                "reason": reason,
                "preview": text[:120].replace("\n", " "),
            })

    lines.append(f"\nTotal reflections: {total}")
    lines.append(f"Flagged as junk: {len(flagged)}")
    pct = (len(flagged) / total * 100) if total > 0 else 0
    lines.append(f"Junk rate: {pct:.1f}%")

    if flagged:
        lines.append(f"\n{'─' * 50}")
        lines.append("FLAGGED RECORDS:")
        for item in flagged:
            lines.append(f"\n  {item['file']}")
            lines.append(f"  Reason: {item['reason']}")
            if verbose:
                lines.append(f"  Preview: {item['preview']}")

    summary = "\n".join(lines)
    return flagged, summary


def main():
    verbose = "--verbose" in sys.argv
    flagged, summary = run_audit(verbose=verbose)

    print(summary)

    # Write log
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    log_dir = REPO_ROOT / "logs" / "reflection_audit"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"audit_{timestamp}.log"
    log_file.write_text(summary, encoding="utf-8")
    print(f"\nLog written to: {log_file}")


if __name__ == "__main__":
    main()
