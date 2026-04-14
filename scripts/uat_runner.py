"""
scripts/uat_runner.py

CLI User Acceptance Test runner for Ember-2.

Loads test plan from scripts/uat_tests.yaml, walks through each test
step one at a time, collects pass/fail/skip results with optional notes,
and writes results to JSON log files.

Usage:
    python scripts/uat_runner.py
    python scripts/uat_runner.py --filter bare_mode   # run only matching tests
    python scripts/uat_runner.py --filter v0.16       # filter by ID or feature
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml")
    sys.exit(1)

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_PLAN = REPO_ROOT / "scripts" / "uat_tests.yaml"
LOG_DIR = REPO_ROOT / "logs"
LATEST_FILE = LOG_DIR / "uat_results_latest.json"
HISTORY_FILE = LOG_DIR / "uat_results_history.json"


def load_tests(filter_term: str | None = None) -> list[dict]:
    """Load test cases from YAML. Optionally filter by ID or feature name."""
    with open(TEST_PLAN, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    tests = data.get("tests", [])
    if filter_term:
        term = filter_term.lower()
        tests = [
            t for t in tests
            if term in t.get("id", "").lower()
            or term in t.get("feature", "").lower()
            or term in t.get("description", "").lower()
        ]
    return tests


def prompt_result() -> str:
    """Prompt for P/F/S until valid input received."""
    while True:
        choice = input("\n  Result — [P]ass / [F]ail / [S]kip: ").strip().upper()
        if choice in ("P", "F", "S"):
            return {"P": "pass", "F": "fail", "S": "skip"}[choice]
        print("  Invalid — enter P, F, or S.")


def prompt_note() -> str:
    """Prompt for optional note. Enter to skip."""
    note = input("  Note (enter to skip): ").strip()
    return note


def run(tests: list[dict]) -> list[dict]:
    """Walk through each test and collect results."""
    results = []
    total = len(tests)

    print(f"\n{'=' * 60}")
    print(f"  Ember-2 UAT Runner — {total} test(s)")
    print(f"{'=' * 60}\n")

    for i, test in enumerate(tests, 1):
        test_id = test.get("id", f"UAT-{i:03d}")
        feature = test.get("feature", "Unknown")
        description = test.get("description", "")
        steps = test.get("steps", "")
        expected = test.get("expected", "")

        print(f"  [{i}/{total}] {test_id}")
        print(f"  Feature:  {feature}")
        print(f"  Desc:     {description}")
        print(f"  Do:       {steps}")
        print(f"  Expect:   {expected}")

        result = prompt_result()
        note = ""
        if result in ("pass", "fail"):
            note = prompt_note()

        results.append({
            "id": test_id,
            "feature": feature,
            "description": description,
            "result": result,
            "note": note,
        })

        print()

    return results


def write_results(results: list[dict]) -> None:
    """Write latest results and append to history."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Overwrite latest
    LATEST_FILE.write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Build summary
    passed = sum(1 for r in results if r["result"] == "pass")
    failed = sum(1 for r in results if r["result"] == "fail")
    skipped = sum(1 for r in results if r["result"] == "skip")
    fail_notes = [
        {"id": r["id"], "feature": r["feature"], "note": r["note"]}
        for r in results if r["result"] == "fail"
    ]

    summary = {
        "timestamp": datetime.now().isoformat(),
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "fail_notes": fail_notes,
    }

    # Append to history
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            history = []
    history.append(summary)
    HISTORY_FILE.write_text(
        json.dumps(history, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return summary


def print_summary(summary: dict) -> None:
    """Print terminal summary."""
    print(f"{'=' * 60}")
    print(f"  RESULTS: {summary['passed']} passed, {summary['failed']} failed, {summary['skipped']} skipped")
    print(f"{'=' * 60}")

    if summary["fail_notes"]:
        print("\n  FAILURES:")
        for fn in summary["fail_notes"]:
            note = f" — {fn['note']}" if fn["note"] else ""
            print(f"    {fn['id']} ({fn['feature']}){note}")

    print(f"\n  Latest: {LATEST_FILE}")
    print(f"  History: {HISTORY_FILE}\n")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Ember-2 CLI UAT runner")
    parser.add_argument("--filter", type=str, default=None, help="Filter tests by ID or feature name")
    args = parser.parse_args()

    if not TEST_PLAN.exists():
        print(f"ERROR: Test plan not found at {TEST_PLAN}")
        sys.exit(1)

    tests = load_tests(args.filter)
    if not tests:
        print("No tests matched the filter." if args.filter else "No tests found in test plan.")
        sys.exit(1)

    results = run(tests)
    summary = write_results(results)
    print_summary(summary)


if __name__ == "__main__":
    main()