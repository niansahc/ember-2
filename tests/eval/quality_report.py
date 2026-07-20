"""
tests/eval/quality_report.py

Metadata-only reporting + baseline-regression gate for the response-quality
framework.

Vault privacy (CLAUDE.md): a report NEVER carries response or turn text. This is
enforced structurally - build_case_report accepts only case id, eval name, a
pass flag, numeric metrics, boolean flags, latency, and word count. There is no
parameter through which vault-derived text could reach disk. Mirrors the
metadata-only pattern in tools/eval_manual.py::_run_auto_battery.
"""

from __future__ import annotations

import json
import os


def build_case_report(
    case_id: str,
    eval_name: str,
    passed: bool,
    metrics: dict,
    flags: dict | None = None,
    latency: float | None = None,
    word_count: int | None = None,
) -> dict:
    """Build a single case's metadata-only report entry.

    metrics are scalars (scores, ratios, deltas, slopes); flags are booleans. No
    response/turn text is accepted, so none can be serialized.
    """
    return {
        "case_id": case_id,
        "eval": eval_name,
        "passed": bool(passed),
        "metrics": {k: v for k, v in metrics.items()},
        "flags": dict(flags or {}),
        "latency": latency,
        "word_count": word_count,
    }


def compare_to_baseline(
    current: dict, baseline: dict, max_drop: float = 0.05
) -> dict:
    """Compare current scalar metrics to a baseline; fail on a drop > max_drop.

    A missing/empty baseline means this is a calibration run: it passes and is
    flagged `calibration` so the caller records a baseline instead of gating on
    one that does not exist yet.
    """
    if not baseline:
        return {"passed": True, "calibration": True, "regressions": {}}

    regressions = {}
    for metric, base_val in baseline.items():
        if metric not in current:
            continue
        drop = base_val - current[metric]
        if drop > max_drop:
            regressions[metric] = {
                "baseline": base_val,
                "current": current[metric],
                "drop": drop,
            }
    return {
        "passed": len(regressions) == 0,
        "calibration": False,
        "regressions": regressions,
    }


def write_report(path: str, report: dict) -> None:
    """Persist a metadata-only report as JSON (ASCII-safe).

    Creates the parent directory if it does not exist - the release gate writes
    to logs/eval_quality/, which may not have been created yet.
    """
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=True)


def load_baseline(path: str) -> dict:
    """Load a baseline metrics file; empty dict if absent (calibration mode)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
