"""
tests/uat/run_uat.py

Interactive UAT release-acceptance runner. Walks the scenarios in
tests/uat/scenarios.yaml (the machine source of truth; docs/UAT.md is the
human-readable mirror), records a verdict + optional note per scenario, and
writes a metadata-only report to logs/uat/<version>-<timestamp>.json.

    python -m tests.uat.run_uat --version 0.18.0

Follows the run_quality.py pattern: the pure functions (parse_verdict,
summarize, build_report, write_report, load_scenarios) are unit-testable; the
interactive loop takes injected input/print/clock so it can be driven by fakes.

Privacy: the report holds scenario ids/names, verdicts, reviewer notes, and
timestamps only. No vault content and no Ember response text ever passes through
this tool - the reviewer types their own observations.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

# Verdict keys. Case-sensitive F vs f distinguishes Fail from flag (matches the
# docs/UAT.md rubric): Fail is release-blocking, flag is a follow-up note.
_VERDICT_MAP = {
    "p": "pass", "P": "pass",
    "F": "fail",
    "f": "flag",
    "s": "skip", "S": "skip",
    "q": "quit", "Q": "quit",
}
_PROMPT = "  [P]ass / [F]ail / [f]lag / [s]kip / [q]uit: "


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_scenarios(path: str) -> list:
    """Load the scenario list from the YAML source of truth."""
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data.get("scenarios", [])


def parse_verdict(raw: str):
    """Map a keypress to a verdict, or None if unrecognized."""
    return _VERDICT_MAP.get((raw or "").strip())


def summarize(records: list) -> dict:
    """Count verdicts across recorded scenarios."""
    counts = {"pass": 0, "fail": 0, "flag": 0, "skip": 0}
    for r in records:
        v = r.get("verdict")
        if v in counts:
            counts[v] += 1
    return counts


def build_report(version: str, records: list, generated_at: str,
                 reviewer: str = "", approved=None) -> dict:
    """Assemble the metadata-only UAT report."""
    return {
        "version": version,
        "generated_at": generated_at,
        "reviewer": reviewer,
        "approved": approved,
        "summary": summarize(records),
        "results": records,
    }


def write_report(path: str, report: dict) -> None:
    """Persist the report as JSON, creating the output directory if needed."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=True)


def _print_scenario(sc: dict, out_fn) -> None:
    out_fn("")
    out_fn(f"=== {sc.get('name', sc.get('id'))} ===")
    if sc.get("setup"):
        out_fn(f"  Setup: {sc['setup'].strip()}")
    if sc.get("actions"):
        out_fn("  Actions:")
        for a in sc["actions"]:
            out_fn(f"    - {a}")
    if sc.get("expected"):
        out_fn(f"  Expected: {sc['expected'].strip()}")


def run_interactive(scenarios: list, in_fn=input, out_fn=print, now_fn=_now) -> list:
    """Walk scenarios, collect verdicts + notes. Returns the record list.

    in_fn/out_fn/now_fn are injectable so tests can drive the loop without a TTY.
    A 'quit' verdict stops the walk immediately (records so far are kept).
    """
    records = []
    for sc in scenarios:
        _print_scenario(sc, out_fn)
        verdict = None
        while verdict is None:
            verdict = parse_verdict(in_fn(_PROMPT))
            if verdict is None:
                out_fn("  unrecognized - use P / F / f / s / q")
        if verdict == "quit":
            break
        note = ""
        if verdict in ("fail", "flag"):
            note = (in_fn("  note: ") or "").strip()
        records.append({
            "scenario_id": sc.get("id"),
            "name": sc.get("name"),
            "verdict": verdict,
            "note": note,
            "timestamp": now_fn(),
        })
    return records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Interactive UAT acceptance runner")
    parser.add_argument("--version", default="unreleased",
                        help="release version being accepted (used in the report + filename)")
    parser.add_argument("--scenarios",
                        default=str(Path(__file__).parent / "scenarios.yaml"))
    parser.add_argument("--out-dir", default="logs/uat")
    args = parser.parse_args(argv)

    scenarios = load_scenarios(args.scenarios)
    print(f"UAT acceptance - version {args.version} - {len(scenarios)} scenarios")
    print("Read each scenario, do the actions, then record a verdict.")

    records = run_interactive(scenarios)
    counts = summarize(records)

    print("")
    print(f"Summary: {counts['pass']} pass  {counts['fail']} fail  "
          f"{counts['flag']} flag  {counts['skip']} skip "
          f"({len(records)}/{len(scenarios)} recorded)")

    reviewer = (input("Reviewer name: ") or "").strip()
    approved = (input("Release approved? [y/N]: ") or "").strip().lower() in ("y", "yes")
    if counts["fail"] > 0 and approved:
        print("  note: approving with FAILs on record.")

    report = build_report(args.version, records, _now(), reviewer, approved)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = str(Path(args.out_dir) / f"{args.version}-{stamp}.json")
    write_report(out_path, report)
    print(f"Report written to {out_path}")

    # Non-zero exit if any scenario failed - a fail is release-blocking.
    return 1 if counts["fail"] > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
