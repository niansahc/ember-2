"""tools/eval/coaching_filter_audit.py

Audit script for coaching filter intervention logs.

Reads logs/coaching_filter/*.json over a configurable time window and
reports stratified statistics so the team can spot drift in:

- Total intervention rate
- Stage breakdown (Stage 0 scripted, Stage 1 deletion, Stage 2 rewrite)
- Intent class distribution
- False-positive rate (coaching filter firing on factual intent classes
  where coaching tone is not the right register)
- Pattern category distribution
- Wasted Stage 2 calls (LLM rewrite produced no change)

Usage:
    python tools/eval/coaching_filter_audit.py
    python tools/eval/coaching_filter_audit.py --days 7

Output is plain ASCII to stdout; no log file is written. Exit code 0
on success.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = REPO_ROOT / "logs" / "coaching_filter"

# Intent classes where coaching/therapeutic register is not the right
# response shape. Filter interventions on these intents are evidence of
# over-firing -- either the is_conversational gate is too broad or the
# pattern set is matching outside its intended scope. Mirrors the list
# the v0.18.0 numbered_structure intent gate suppresses.
FACTUAL_INTENT_CLASSES: frozenset[str] = frozenset({
    "web_search",
    "factual_recall",
    "recent",
    "status_state",
})


def _parse_filename_timestamp(name: str) -> datetime | None:
    """Parse YYYY-MM-DDTHH-MM-SSZ.json filename to a UTC datetime.

    Returns None if the filename does not match the expected format.
    """
    stem = name.removesuffix(".json")
    if not stem.endswith("Z"):
        return None
    stem = stem[:-1]  # strip trailing Z
    try:
        # Filename uses hyphens for both date and time components.
        dt = datetime.strptime(stem, "%Y-%m-%dT%H-%M-%S")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _collect_log_files(log_dir: Path, cutoff: datetime) -> list[Path]:
    """Return log files with filename timestamp >= cutoff."""
    if not log_dir.is_dir():
        return []
    matching: list[Path] = []
    for path in sorted(log_dir.glob("*.json")):
        ts = _parse_filename_timestamp(path.name)
        if ts is None:
            continue
        if ts >= cutoff:
            matching.append(path)
    return matching


def _format_row(label: str, count: int, total: int, suffix: str = "") -> str:
    """Render a single line in the breakdown tables."""
    pct = (count / total * 100.0) if total else 0.0
    return f"  {label:<28s} {count:>4d}  ({pct:5.1f}%){suffix}"


def aggregate(log_files: list[Path]) -> dict:
    """Walk the log files and accumulate counters."""
    by_stage: Counter = Counter()
    by_intent: Counter = Counter()
    by_pattern: Counter = Counter()
    false_positive_count = 0
    wasted_stage_2 = 0
    parse_errors = 0

    for path in log_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            parse_errors += 1
            continue

        stage = data.get("stage")
        if not isinstance(stage, int):
            parse_errors += 1
            continue

        intent_class = data.get("intent_class") or "unknown"
        changed = bool(data.get("changed"))
        patterns = data.get("patterns") or []

        by_stage[stage] += 1
        by_intent[intent_class] += 1

        if intent_class in FACTUAL_INTENT_CLASSES:
            false_positive_count += 1

        if stage == 2 and not changed:
            wasted_stage_2 += 1

        for entry in patterns:
            pattern_name = entry.get("pattern") or "unknown"
            by_pattern[pattern_name] += 1

    total = sum(by_stage.values())
    return {
        "total": total,
        "by_stage": by_stage,
        "by_intent": by_intent,
        "by_pattern": by_pattern,
        "false_positive_count": false_positive_count,
        "wasted_stage_2": wasted_stage_2,
        "parse_errors": parse_errors,
    }


def render_report(stats: dict, days: int, cutoff: datetime) -> str:
    """Render the ASCII summary table."""
    total = stats["total"]
    lines: list[str] = []
    lines.append("Coaching Filter Audit")
    lines.append("=====================")
    now = datetime.now(timezone.utc)
    lines.append(
        f"Window: last {days} days "
        f"({cutoff.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')} UTC)"
    )
    lines.append(f"Total interventions: {total}")
    if stats["parse_errors"]:
        lines.append(f"Parse errors (skipped): {stats['parse_errors']}")
    lines.append("")

    if total == 0:
        lines.append("No intervention logs in window.")
        return "\n".join(lines)

    # By stage
    lines.append("By stage:")
    for stage in sorted(stats["by_stage"].keys()):
        count = stats["by_stage"][stage]
        lines.append(_format_row(f"Stage {stage}", count, total))
    lines.append("")

    # By intent class
    lines.append("By intent class:")
    for intent, count in sorted(
        stats["by_intent"].items(), key=lambda kv: kv[1], reverse=True,
    ):
        suffix = "  [FP]" if intent in FACTUAL_INTENT_CLASSES else ""
        lines.append(_format_row(intent, count, total, suffix))
    lines.append("")

    # False-positive summary
    fp = stats["false_positive_count"]
    fp_pct = (fp / total * 100.0) if total else 0.0
    lines.append(
        f"False positives (filter on factual intent): {fp} ({fp_pct:.1f}%)"
    )

    # Wasted Stage 2
    stage_2_total = stats["by_stage"].get(2, 0)
    wasted = stats["wasted_stage_2"]
    wasted_pct = (wasted / stage_2_total * 100.0) if stage_2_total else 0.0
    lines.append(
        f"Wasted Stage 2 calls (changed=False): "
        f"{wasted} ({wasted_pct:.1f}% of Stage 2)"
    )
    lines.append("")

    # By pattern category
    pattern_total = sum(stats["by_pattern"].values())
    if pattern_total:
        lines.append("By pattern category:")
        for pattern, count in sorted(
            stats["by_pattern"].items(),
            key=lambda kv: kv[1],
            reverse=True,
        ):
            lines.append(_format_row(pattern, count, pattern_total))

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit coaching filter intervention logs. Stratifies by intent "
            "class, pattern category, and stage. Flags false positives on "
            "factual intent classes and wasted Stage 2 LLM calls."
        ),
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Log window in days (default: 30).",
    )
    args = parser.parse_args(argv)

    if args.days <= 0:
        print("ERROR: --days must be a positive integer.", file=sys.stderr)
        return 2

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    log_files = _collect_log_files(LOG_DIR, cutoff)
    stats = aggregate(log_files)
    report = render_report(stats, args.days, cutoff)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
