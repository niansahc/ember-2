"""
tests/eval/test_quality_report.py

Unit tests for the metadata-only report builder and the baseline-regression gate.
The structural privacy guarantee: build_case_report has NO parameter for response
or turn text, so a report cannot carry vault-derived response content to disk.
"""

import json

from tests.eval.quality_report import build_case_report, compare_to_baseline


def test_report_is_metadata_only_and_cannot_carry_response_text():
    # The eval processed a response containing sensitive content, but the report
    # is built from metrics only - there is no field to leak it.
    case = build_case_report(
        case_id="grounding_1",
        eval_name="grounding",
        passed=True,
        metrics={"supported_ratio": 1.0, "confabulated_count": 0},
        flags={},
        latency=1.23,
        word_count=42,
    )
    blob = json.dumps(case)
    assert "diary" not in blob and "Boston" not in blob  # never passed in
    assert case["case_id"] == "grounding_1"
    assert case["eval"] == "grounding"
    assert case["metrics"]["supported_ratio"] == 1.0
    assert case["word_count"] == 42


def test_compare_to_baseline_flags_regression():
    baseline = {"supported_ratio": 0.95, "pass_rate": 1.0}
    current = {"supported_ratio": 0.80, "pass_rate": 1.0}  # 0.15 drop > 0.05
    result = compare_to_baseline(current, baseline, max_drop=0.05)
    assert result["passed"] is False
    assert "supported_ratio" in result["regressions"]


def test_compare_to_baseline_passes_within_tolerance():
    result = compare_to_baseline({"score": 3.47}, {"score": 3.5}, max_drop=0.05)
    assert result["passed"] is True
    assert result["regressions"] == {}


def test_compare_to_baseline_first_run_is_calibration_not_failure():
    # No baseline yet: first runs calibrate, they do not gate (gating a noisy
    # baseline would block releases).
    result = compare_to_baseline({"score": 3.0}, {}, max_drop=0.05)
    assert result["passed"] is True
    assert result["calibration"] is True


def test_compare_to_baseline_improvement_passes():
    result = compare_to_baseline({"score": 3.9}, {"score": 3.5}, max_drop=0.05)
    assert result["passed"] is True
