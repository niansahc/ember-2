"""tests/test_uat_runner.py

Coverage for the targeted re-run feature in scripts/uat_runner.py.
The runner is a CLI tool, but its load/merge/summary helpers are
importable and unit-testable without invoking input() prompts.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.uat_runner as runner


# ---------------------------------------------------------------------------
# parse_ids — input parsing
# ---------------------------------------------------------------------------


def test_parse_ids_returns_none_when_arg_absent():
    assert runner.parse_ids(None) is None


def test_parse_ids_splits_comma_list():
    assert runner.parse_ids("B-MEM-001,B-WEB-002") == ["B-MEM-001", "B-WEB-002"]


def test_parse_ids_strips_whitespace():
    assert runner.parse_ids(" B-MEM-001 , B-WEB-002 ") == ["B-MEM-001", "B-WEB-002"]


def test_parse_ids_drops_empty_entries():
    assert runner.parse_ids("B-MEM-001,,B-WEB-002, ") == ["B-MEM-001", "B-WEB-002"]


# ---------------------------------------------------------------------------
# load_tests — exact-ID filtering, plus coexistence with --filter
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_test_plan(tmp_path: Path, monkeypatch):
    """Patch TEST_PLAN onto a tmp YAML so tests don't depend on the real file."""
    yaml_path = tmp_path / "uat_tests.yaml"
    yaml_path.write_text(
        """
tests:
  - id: B-MEM-001
    feature: memory
    description: profile recall
    steps: ask
    expected: name returned
  - id: B-MEM-002
    feature: memory
    description: state recall
    steps: ask
    expected: state returned
  - id: B-WEB-001
    feature: web_search
    description: weather
    steps: ask
    expected: temp shown
standalone_tests:
  - id: B-STA-001
    feature: standalone
    description: manual
    steps: do
    expected: x
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "TEST_PLAN", yaml_path)
    return yaml_path


def test_ids_filters_to_exact_ids_only(fake_test_plan):
    tests, standalone = runner.load_tests(ids=["B-MEM-001", "B-WEB-001"])
    assert {t["id"] for t in tests} == {"B-MEM-001", "B-WEB-001"}
    # B-MEM-002 must NOT appear despite the same prefix
    assert "B-MEM-002" not in {t["id"] for t in tests}
    assert standalone == []


def test_ids_partial_id_does_not_match(fake_test_plan):
    """`B-MEM` substring should not match `B-MEM-001` — exact match required."""
    tests, standalone = runner.load_tests(ids=["B-MEM"])
    assert tests == []
    assert standalone == []


def test_ids_matches_standalone(fake_test_plan):
    tests, standalone = runner.load_tests(ids=["B-STA-001"])
    assert tests == []
    assert {s["id"] for s in standalone} == {"B-STA-001"}


def test_filter_alone_still_works(fake_test_plan):
    """Existing --filter behavior must not regress."""
    tests, _ = runner.load_tests(filter_term="memory")
    assert {t["id"] for t in tests} == {"B-MEM-001", "B-MEM-002"}


def test_ids_and_filter_both_apply(fake_test_plan):
    """When both are provided, --ids picks the exact set then --filter narrows
    by substring on id/feature/description."""
    tests, _ = runner.load_tests(ids=["B-MEM-001", "B-WEB-001"], filter_term="memory")
    assert {t["id"] for t in tests} == {"B-MEM-001"}


# ---------------------------------------------------------------------------
# merge_with_latest — replacement / preservation
# ---------------------------------------------------------------------------


def _write_latest(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding="utf-8")


def test_merge_replaces_only_matching_records(tmp_path: Path):
    """Records whose IDs are in rerun_ids get replaced; others preserved verbatim."""
    latest = tmp_path / "uat_results_latest.json"
    _write_latest(latest, [
        {"id": "B-MEM-001", "feature": "memory", "description": "old", "result": "fail", "note": "previous"},
        {"id": "B-MEM-002", "feature": "memory", "description": "untouched", "result": "pass", "note": ""},
        {"id": "B-WEB-001", "feature": "web_search", "description": "untouched", "result": "skip", "note": ""},
    ])

    new_results = [
        {"id": "B-MEM-001", "feature": "memory", "description": "new", "result": "pass", "note": "fixed"},
    ]

    merged = runner.merge_with_latest(new_results, rerun_ids=["B-MEM-001"], latest_file=latest)

    by_id = {r["id"]: r for r in merged}
    assert by_id["B-MEM-001"]["result"] == "pass"  # replaced
    assert by_id["B-MEM-001"]["note"] == "fixed"
    assert by_id["B-MEM-002"]["note"] == ""        # preserved verbatim
    assert by_id["B-MEM-002"]["result"] == "pass"
    assert by_id["B-WEB-001"]["result"] == "skip"  # preserved verbatim


def test_merge_appends_new_ids_not_in_existing(tmp_path: Path):
    """If --ids names a test not yet in the latest file, it gets appended."""
    latest = tmp_path / "uat_results_latest.json"
    _write_latest(latest, [
        {"id": "B-MEM-001", "feature": "memory", "description": "x", "result": "pass", "note": ""},
    ])

    new_results = [
        {"id": "B-NEW-001", "feature": "new", "description": "y", "result": "pass", "note": ""},
    ]

    merged = runner.merge_with_latest(new_results, rerun_ids=["B-NEW-001"], latest_file=latest)
    assert {r["id"] for r in merged} == {"B-MEM-001", "B-NEW-001"}


def test_merge_handles_missing_latest_file(tmp_path: Path):
    """No existing file → merged result is just the new results (no error)."""
    latest = tmp_path / "uat_results_latest.json"
    new_results = [
        {"id": "B-MEM-001", "feature": "memory", "description": "x", "result": "pass", "note": ""},
    ]
    merged = runner.merge_with_latest(new_results, rerun_ids=["B-MEM-001"], latest_file=latest)
    assert merged == new_results


def test_merge_preserves_record_order(tmp_path: Path):
    """Existing records stay in their original positions; replacements happen
    in place (no reordering)."""
    latest = tmp_path / "uat_results_latest.json"
    _write_latest(latest, [
        {"id": "A", "result": "pass"},
        {"id": "B", "result": "fail"},
        {"id": "C", "result": "skip"},
    ])
    new_results = [{"id": "B", "result": "pass"}]
    merged = runner.merge_with_latest(new_results, rerun_ids=["B"], latest_file=latest)
    assert [r["id"] for r in merged] == ["A", "B", "C"]
    assert merged[1]["result"] == "pass"


# ---------------------------------------------------------------------------
# Summary — totals reflect merged file, not just re-run subset
# ---------------------------------------------------------------------------


def test_summary_totals_reflect_merged_file(tmp_path: Path):
    """After merge, write_results' summary covers ALL records, not just
    the re-run subset. Total = full file size."""
    latest = tmp_path / "uat_results_latest.json"
    history = tmp_path / "uat_results_history.json"
    _write_latest(latest, [
        {"id": "A", "feature": "f", "description": "d", "result": "pass", "note": ""},
        {"id": "B", "feature": "f", "description": "d", "result": "fail", "note": "old"},
        {"id": "C", "feature": "f", "description": "d", "result": "pass", "note": ""},
    ])

    new_results = [
        {"id": "B", "feature": "f", "description": "d", "result": "pass", "note": "fixed"},
    ]
    merged = runner.merge_with_latest(new_results, rerun_ids=["B"], latest_file=latest)

    # Use the helper directly (avoids touching the module-level LOG_DIR / files).
    summary = runner._summary_from_results(merged)

    assert summary["total"] == 3            # full file, not re-run subset
    assert summary["passed"] == 3           # all three now pass
    assert summary["failed"] == 0
    assert summary["fail_notes"] == []


# ---------------------------------------------------------------------------
# CLI exit codes
# ---------------------------------------------------------------------------


def test_ids_no_matches_exits_1(tmp_path: Path):
    """`python -m scripts.uat_runner --ids DOES-NOT-EXIST` exits 1 with a
    clear error message — typos must be loud, not silent."""
    # Use a minimal YAML so the test doesn't depend on the real test plan.
    plan = tmp_path / "uat_tests.yaml"
    plan.write_text(
        "tests:\n  - id: REAL-001\n    feature: x\n    description: y\n",
        encoding="utf-8",
    )

    runner_path = Path(runner.__file__).resolve()
    repo_root = runner_path.parents[1]
    env_overrides = {
        "PYTHONPATH": str(repo_root),
    }

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.path.insert(0, %r); "
                "import scripts.uat_runner as r; "
                "r.TEST_PLAN = __import__('pathlib').Path(%r); "
                "sys.argv = ['uat_runner', '--ids', 'BOGUS-001']; "
                "r.main()"
            ) % (str(repo_root), str(plan)),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 1, f"expected exit 1, got {proc.returncode}; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "matched no tests" in proc.stdout.lower() or "matched no tests" in proc.stderr.lower()
