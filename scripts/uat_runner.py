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
    python scripts/uat_runner.py --ids B-MEM-001,B-WEB-002  # re-run specific IDs
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


def parse_ids(raw: str | None) -> list[str] | None:
    """Parse a comma-separated --ids value into a clean list. Returns None
    when the arg was not provided. Empty/whitespace-only entries are dropped."""
    if raw is None:
        return None
    parts = [p.strip() for p in raw.split(",")]
    return [p for p in parts if p]


def load_tests(
    filter_term: str | None = None,
    ids: list[str] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Load test cases from YAML. Returns (tests, standalone_tests).

    `ids` (when non-empty) restricts to tests whose `id` field exactly
    matches one of the provided IDs. `filter_term` (substring match on
    id/feature/description) can coexist with `ids` and applies as an
    additional constraint after the exact-ID filter.
    """
    with open(TEST_PLAN, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    tests = data.get("tests", [])
    standalone = data.get("standalone_tests", [])

    if ids:
        id_set = set(ids)
        tests = [t for t in tests if t.get("id") in id_set]
        standalone = [t for t in standalone if t.get("id") in id_set]

    if filter_term:
        term = filter_term.lower()
        def matches(t):
            return (
                term in t.get("id", "").lower()
                or term in t.get("feature", "").lower()
                or term in t.get("description", "").lower()
            )
        tests = [t for t in tests if matches(t)]
        standalone = [t for t in standalone if matches(t)]
    return tests, standalone


def display_standalone(standalone: list[dict]) -> None:
    """Display standalone tests without prompting for results."""
    if not standalone:
        return
    print(f"\n{'=' * 60}")
    print("  STANDALONE TESTS")
    print("  The following tests require separate manual verification.")
    print("  Note your results and update uat_results_latest.json manually.")
    print(f"{'=' * 60}\n")
    for i, test in enumerate(standalone, 1):
        print(f"  [{i}/{len(standalone)}] {test.get('id')}")
        print(f"  Feature:  {test.get('feature', '')}")
        print(f"  Desc:     {test.get('description', '')}")
        print(f"  Do:       {test.get('steps', '')}")
        print(f"  Expect:   {test.get('expected', '')}\n")


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


def merge_with_latest(
    new_results: list[dict],
    rerun_ids: list[str],
    latest_file: Path = LATEST_FILE,
) -> list[dict]:
    """Merge `new_results` from a targeted re-run back into the existing
    latest-file records.

    Records in the existing file whose IDs are in `rerun_ids` are replaced
    by the new result. All other existing records pass through unchanged.
    Records in `new_results` whose IDs are NOT already in the file are
    appended at the end (covers the case where --ids names a test that
    hadn't been run before).

    Returns the merged result list. Does not write to disk — the caller
    decides when to persist via write_results().
    """
    existing: list[dict] = []
    if latest_file.exists():
        try:
            loaded = json.loads(latest_file.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                existing = loaded
        except (json.JSONDecodeError, OSError):
            existing = []

    new_by_id = {r["id"]: r for r in new_results if r.get("id")}
    rerun_set = set(rerun_ids)

    merged: list[dict] = []
    seen_ids: set[str] = set()
    for record in existing:
        rid = record.get("id")
        if rid in rerun_set and rid in new_by_id:
            merged.append(new_by_id[rid])
        else:
            merged.append(record)
        if rid is not None:
            seen_ids.add(rid)

    for r in new_results:
        rid = r.get("id")
        if rid is not None and rid not in seen_ids:
            merged.append(r)

    return merged


def _summary_from_results(results: list[dict]) -> dict:
    """Compute the summary block used by both write_results and tests."""
    passed = sum(1 for r in results if r.get("result") == "pass")
    failed = sum(1 for r in results if r.get("result") == "fail")
    skipped = sum(1 for r in results if r.get("result") == "skip")
    fail_notes = [
        {"id": r.get("id"), "feature": r.get("feature"), "note": r.get("note", "")}
        for r in results if r.get("result") == "fail"
    ]
    return {
        "timestamp": datetime.now().isoformat(),
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "fail_notes": fail_notes,
    }


def write_results(results: list[dict]) -> dict:
    """Write the full result list to LATEST_FILE and append summary to history."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    LATEST_FILE.write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = _summary_from_results(results)

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
    parser.add_argument("--filter", type=str, default=None, help="Filter tests by ID or feature name (substring)")
    parser.add_argument(
        "--ids",
        type=str,
        default=None,
        help="Comma-separated test IDs to re-run (e.g. B-MEM-001,B-WEB-002). "
             "Results merge into uat_results_latest.json instead of overwriting.",
    )
    args = parser.parse_args()

    if not TEST_PLAN.exists():
        print(f"ERROR: Test plan not found at {TEST_PLAN}")
        sys.exit(1)

    ids = parse_ids(args.ids)
    tests, standalone = load_tests(filter_term=args.filter, ids=ids)

    if ids and not tests and not standalone:
        # Targeted re-run found zero matching tests — make this loud and
        # non-zero so a typo'd ID list doesn't silently no-op.
        print(
            f"ERROR: --ids matched no tests. Provided: {', '.join(ids)}\n"
            "  Check scripts/uat_tests.yaml for valid IDs."
        )
        sys.exit(1)

    if not tests and not standalone:
        print("No tests matched the filter." if args.filter else "No tests found in test plan.")
        sys.exit(1)

    if tests:
        results = run(tests)

        if ids:
            # Targeted re-run: merge with the existing latest-file and
            # write the merged total back. Summary reflects the merged
            # picture, not just the re-run subset, so the printed output
            # remains a meaningful snapshot of the full UAT state.
            merged = merge_with_latest(results, rerun_ids=ids)
            summary = write_results(merged)
        else:
            summary = write_results(results)

        print_summary(summary)

    display_standalone(standalone)


if __name__ == "__main__":
    main()
