"""tools/eval/policy_trigger_audit.py

Audit script for the high_risk_pattern signal in
src/safety/policy_service.py over a configurable log window.

The high_risk_pattern signal substring-matches three keywords against
(user_message + draft_response).lower() with no contextual gates:
"step by step", "exact steps", "without getting caught". A match
fires the full LLM-assisted constitutional review (~15-20s on
qwen3:8b). When the trigger is the sole reason review fired AND the
review outcome is consistently "allow", the keyword is over-triggering
and is a candidate for removal.

This script reports the data needed to make that decision per keyword.
It does NOT decide -- decision rule lives in the calling commit
message and is applied by humans reviewing the output.

Decision rule (documented for completeness):
  REMOVE candidate if all three hold on solo-fires:
    - >= 95% of outcomes are "allow"
    - zero "refuse_redirect" outcomes
    - solo-fires count >= 20 (sample-size guard)
  Otherwise KEEP the keyword.

Solo-fire = high_risk_pattern triggered AND triggered_by contains no
other signal. Co-occurring fires are noise for the decision because
removing the keyword leaves the other signal still firing review on
those logs -- runtime behavior does not change.

Privacy: this script reads user_message and draft_response from the
logs to compute substring matches and source split, but emits ONLY
counts and structural metadata to stdout. No literal query or
response text is ever written to the output. Vault privacy rule
preserved.

Usage:
    python tools/eval/policy_trigger_audit.py
    python tools/eval/policy_trigger_audit.py --days 7
    python tools/eval/policy_trigger_audit.py --keyword "step by step"
    python tools/eval/policy_trigger_audit.py --all-keywords

Output is plain ASCII to stdout. Exit code 0 on success.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = REPO_ROOT / "logs" / "safety_reviews"

# Keywords currently in src/safety/policy_service.py
# :_contains_high_risk_pattern (lines 186-192). Lower-cased; matched
# as substrings in (user_message + draft_response).lower().
HIGH_RISK_KEYWORDS: tuple[str, ...] = (
    "step by step",
    "exact steps",
    "without getting caught",
)


def _parse_filename_timestamp(name: str) -> datetime | None:
    """Parse YYYY-MM-DDTHH-MM-SSZ.json filename to a UTC datetime."""
    stem = name.removesuffix(".json")
    if not stem.endswith("Z"):
        return None
    stem = stem[:-1]
    try:
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


def _normalize_log(data: dict) -> tuple[dict, dict]:
    """Return (trigger, review) dicts, handling backward-compat with
    the older single-'safety' top-level key. Mirrors the shim in
    tools/view_safety_logs.py."""
    trigger = data.get("trigger")
    review = data.get("review")
    if trigger is None:
        safety = data.get("safety", {})
        trigger = {
            "triggered": safety.get("triggered", False),
            "triggered_by": safety.get("triggered_by", []),
        }
    if review is None:
        safety = data.get("safety", {})
        review = {
            "outcome": safety.get("outcome", "unknown"),
        }
    return trigger, review


def aggregate(
    log_files: list[Path], keywords: tuple[str, ...],
) -> dict:
    """Walk the log files and accumulate per-keyword counters."""
    parse_errors = 0
    total_logs_scanned = 0
    total_high_risk_fires = 0

    per_keyword: dict[str, dict] = {
        kw: {
            "total_fires": 0,
            "solo_fires": 0,
            "solo_outcomes": Counter(),
            "source_user_only": 0,
            "source_draft_only": 0,
            "source_both": 0,
        }
        for kw in keywords
    }

    for path in log_files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            parse_errors += 1
            continue

        total_logs_scanned += 1
        trigger, review = _normalize_log(data)
        triggered_by = trigger.get("triggered_by", [])
        if not isinstance(triggered_by, list):
            triggered_by = []

        if "high_risk_pattern" not in triggered_by:
            continue

        total_high_risk_fires += 1

        # Solo-fire = ONLY signal in triggered_by is high_risk_pattern.
        # Cleanest definition; matches the audit decision rule.
        is_solo = triggered_by == ["high_risk_pattern"]

        user_msg = (data.get("user_message") or "").lower()
        draft = (data.get("draft_response") or "").lower()
        outcome = review.get("outcome", "unknown")

        for kw in keywords:
            kw_lower = kw.lower()
            in_user = kw_lower in user_msg
            in_draft = kw_lower in draft
            if not (in_user or in_draft):
                continue

            bucket = per_keyword[kw]
            bucket["total_fires"] += 1
            if is_solo:
                bucket["solo_fires"] += 1
                bucket["solo_outcomes"][outcome] += 1
                if in_user and in_draft:
                    bucket["source_both"] += 1
                elif in_user:
                    bucket["source_user_only"] += 1
                else:
                    bucket["source_draft_only"] += 1

    return {
        "total_logs_scanned": total_logs_scanned,
        "total_high_risk_fires": total_high_risk_fires,
        "parse_errors": parse_errors,
        "per_keyword": per_keyword,
    }


def _decision(bucket: dict) -> str:
    """Return decision string per the threshold rule."""
    solo = bucket["solo_fires"]
    outcomes = bucket["solo_outcomes"]
    allow = outcomes.get("allow", 0)
    refuse = outcomes.get("refuse_redirect", 0)
    allow_pct = (allow / solo * 100.0) if solo else 0.0

    if solo == 0:
        return "INCONCLUSIVE: zero solo-fires in window. Keep keyword."
    if solo < 20:
        return (
            f"INCONCLUSIVE: solo_fires={solo} below sample-size guard "
            f"(N>=20). Keep keyword."
        )
    if refuse > 0:
        return (
            f"KEEP: saw {refuse} refuse_redirect on solo-fires. "
            f"Keyword is doing real safety work."
        )
    if allow_pct >= 95.0:
        return (
            f"REMOVE candidate: {allow_pct:.1f}% allow, zero "
            f"refuse_redirect on N={solo} solo-fires."
        )
    return (
        f"KEEP: {allow_pct:.1f}% allow below 95% threshold; "
        f"keyword catches non-trivial revise traffic."
    )


def render_report(
    stats: dict,
    days: int,
    cutoff: datetime,
    keywords: tuple[str, ...],
) -> str:
    """Render the ASCII summary tables."""
    lines: list[str] = []
    lines.append("Policy Trigger Audit: _contains_high_risk_pattern()")
    lines.append("===================================================")
    now = datetime.now(timezone.utc)
    lines.append(
        f"Window: last {days} days "
        f"({cutoff.strftime('%Y-%m-%d')} to "
        f"{now.strftime('%Y-%m-%d')} UTC)"
    )
    lines.append(f"Total reviews scanned: {stats['total_logs_scanned']}")
    lines.append(
        f"Total high_risk_pattern fires: {stats['total_high_risk_fires']}"
    )
    if stats["parse_errors"]:
        lines.append(f"Parse errors (skipped): {stats['parse_errors']}")
    lines.append("")
    lines.append(
        "Decision rule: REMOVE if >=95% allow AND zero refuse_redirect on "
        "solo-fires AND solo_fires>=20."
    )
    lines.append(
        "Solo-fire = triggered_by == ['high_risk_pattern'] exactly. "
        "Co-occurring fires excluded."
    )
    lines.append("")

    for kw in keywords:
        bucket = stats["per_keyword"][kw]
        total = bucket["total_fires"]
        solo = bucket["solo_fires"]
        outcomes = bucket["solo_outcomes"]
        allow = outcomes.get("allow", 0)
        revise = outcomes.get("revise", 0)
        refuse = outcomes.get("refuse_redirect", 0)
        unknown = sum(
            v for k, v in outcomes.items()
            if k not in {"allow", "revise", "refuse_redirect"}
        )

        lines.append(f"Keyword: \"{kw}\"")
        lines.append("-" * (len(kw) + 11))
        lines.append(
            f"  Total high_risk_pattern fires containing keyword: {total}"
        )
        lines.append(
            f"  Solo-fires (no other signal):                     {solo}"
        )

        if solo > 0:
            allow_pct = allow / solo * 100.0
            revise_pct = revise / solo * 100.0
            refuse_pct = refuse / solo * 100.0
            lines.append("  Outcome on solo-fires:")
            lines.append(
                f"    allow:           {allow:>4d}  ({allow_pct:5.1f}%)"
            )
            lines.append(
                f"    revise:          {revise:>4d}  ({revise_pct:5.1f}%)"
            )
            lines.append(
                f"    refuse_redirect: {refuse:>4d}  ({refuse_pct:5.1f}%)"
            )
            if unknown:
                u_pct = unknown / solo * 100.0
                lines.append(
                    f"    unknown:         {unknown:>4d}  ({u_pct:5.1f}%)"
                )
            lines.append("  Source split (solo-fires):")
            lines.append(
                f"    user_message only:   {bucket['source_user_only']}"
            )
            lines.append(
                f"    draft_response only: {bucket['source_draft_only']}"
            )
            lines.append(
                f"    both sides:          {bucket['source_both']}"
            )

        lines.append(f"  Decision: {_decision(bucket)}")
        lines.append("")

    if "without getting caught" in keywords:
        lines.append(
            "Note: \"without getting caught\" is also matched by "
            "_contains_dual_use_signal()"
        )
        lines.append(
            "(src/safety/policy_service.py:182). Removing it from "
            "_contains_high_risk_pattern()"
        )
        lines.append(
            "has zero behavioral effect on any log where dual_use also "
            "triggered -- in those"
        )
        lines.append(
            "logs the trigger fires via dual_use regardless. The keyword "
            "exists in two signals;"
        )
        lines.append(
            "removal from one is effectively a deduplication unless "
            "high_risk fires solo."
        )

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Audit policy_service.py:_contains_high_risk_pattern() trigger "
            "logs. Reports per-keyword solo-fire counts and outcome "
            "distribution so the team can decide whether each keyword "
            "should be removed."
        ),
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Log window in days (default: 30).",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--keyword", type=str, default=None,
        help=(
            "Audit a single keyword (substring; lowercased before matching). "
            "Mutually exclusive with --all-keywords."
        ),
    )
    group.add_argument(
        "--all-keywords", action="store_true",
        help=(
            "Audit all three keywords in _contains_high_risk_pattern. "
            "Default behavior."
        ),
    )
    args = parser.parse_args(argv)

    if args.days <= 0:
        print("ERROR: --days must be a positive integer.", file=sys.stderr)
        return 2

    if args.keyword is not None:
        keywords: tuple[str, ...] = (args.keyword.lower(),)
    else:
        keywords = HIGH_RISK_KEYWORDS

    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)
    log_files = _collect_log_files(LOG_DIR, cutoff)
    stats = aggregate(log_files, keywords)
    report = render_report(stats, args.days, cutoff, keywords)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
