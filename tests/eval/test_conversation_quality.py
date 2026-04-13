"""
tests/eval/test_conversation_quality.py

Golden case evaluation with multi-run averaging.

Usage:
    pytest tests/eval/ -m eval -v --tb=short            # single run (default)
    pytest tests/eval/ -m eval -v --tb=short --runs 3   # 3-run average (recommended)

Multi-run mode runs each golden case N times. Binary flags pass only
if they fire on fewer than 30% of runs. Scalar dimensions pass only
if the average across runs meets the 3/4 floor.
"""

import pytest
from tests.eval.harness import (
    EmberEvalHarness,
    MultiRunResult,
    load_baseline_scores,
    FLAG_MIN_FIRES,
    DIMENSION_SCORE_FLOOR,
)
from tests.eval.judge import ClaudeJudge
from tests.eval.golden_dataset import GOLDEN_CASES

pytestmark = pytest.mark.eval

harness = EmberEvalHarness(ollama_base_url="http://localhost:11434")
judge = ClaudeJudge(model="claude-haiku-4-5")


@pytest.fixture(scope="session")
def judge_client():
    return judge


@pytest.fixture(scope="session")
def num_runs(request):
    return request.config.getoption("--runs")


@pytest.fixture(scope="session")
def all_results(judge_client, num_runs):
    """Run all golden cases and collect MultiRunResults for the summary table."""
    results: dict[str, MultiRunResult] = {}
    for case in GOLDEN_CASES:
        multi = harness.run_case_multi(case, judge_client, num_runs)
        results[case["id"]] = multi
    return results


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["id"] for c in GOLDEN_CASES])
def test_golden_case(case, all_results):
    multi = all_results[case["id"]]

    # Binary flags: pass if flag fired fewer than FLAG_MIN_FIRES times.
    # A single fire across N runs is stochastic noise, not a pattern.
    flag_results = multi.flag_passes(case["expected_failures_absent"])
    for flag, passed in flag_results.items():
        if not passed:
            count = multi.flag_counts.get(flag, 0)
            reasons = [
                r.get(flag, "")
                for r in multi.all_reasoning
                if r.get(flag)
            ]
            reason_sample = reasons[0] if reasons else "no reasoning captured"
            pytest.fail(
                f"{case['id']}: {flag} fired {count}/{multi.num_runs} runs "
                f"(min {FLAG_MIN_FIRES} to fail) — {reason_sample}"
            )

    # Scalar dimensions: pass if average >= 3
    dim_results = multi.dimension_passes()
    dim_avgs = multi.dimension_averages()
    for dim, passed in dim_results.items():
        if not passed:
            # Gather reasoning from runs
            reasons = [
                r.get(dim, "")
                for r in multi.all_reasoning
                if r.get(dim)
            ]
            reason_sample = reasons[0] if reasons else "no reasoning captured"
            pytest.fail(
                f"{case['id']}: {dim} averaged {dim_avgs[dim]:.1f}/4 across "
                f"{multi.num_runs} runs (floor: {DIMENSION_SCORE_FLOOR}) — {reason_sample}"
            )


def test_summary_table(all_results, capsys):
    """Print a summary table of all golden case results."""
    lines = [
        "",
        "=" * 100,
        f"{'CASE':<14} {'RUNS':>4}  {'STATUS':<6}  {'DIMENSIONS':<40}  {'FLAGS'}",
        "-" * 100,
    ]

    total_pass = 0
    total = len(GOLDEN_CASES)

    for case in GOLDEN_CASES:
        multi = all_results[case["id"]]
        summary = multi.summary_line(case["expected_failures_absent"])
        status = "PASS" if multi.passed(case["expected_failures_absent"]) else "FAIL"
        if status == "PASS":
            total_pass += 1

        dim_avgs = multi.dimension_averages()
        dim_str = " ".join(f"{d}={v:.1f}" for d, v in sorted(dim_avgs.items()))

        failed_flags = [
            f for f in case["expected_failures_absent"]
            if multi.flag_counts.get(f, 0) >= FLAG_MIN_FIRES
        ]
        flag_str = ", ".join(
            f"{f}({multi.flag_counts[f]}/{multi.num_runs})" for f in failed_flags
        ) if failed_flags else "clean"

        lines.append(
            f"{case['id']:<14} {multi.num_runs:>4}  {status:<6}  {dim_str:<40}  {flag_str}"
        )

    lines.append("-" * 100)
    lines.append(f"TOTAL: {total_pass}/{total} passed ({total_pass/total:.0%})")
    lines.append("=" * 100)

    # Print to stdout so pytest -s shows it
    print("\n".join(lines))


def test_no_regression_vs_baseline(all_results):
    """Compare current multi-run averages against saved baseline."""
    baseline = load_baseline_scores()
    if not baseline:
        pytest.skip("No baseline_scores.json found — skipping regression check")

    # Aggregate current scores across all cases
    current: dict[str, list[float]] = {}
    for case in GOLDEN_CASES:
        multi = all_results[case["id"]]
        for dim, avg in multi.dimension_averages().items():
            current.setdefault(dim, []).append(avg)

    current_avgs = {dim: sum(v) / len(v) for dim, v in current.items()}

    for metric in baseline:
        if metric not in current_avgs:
            continue
        delta = baseline[metric] - current_avgs[metric]
        assert delta <= 0.05, f"Regression on {metric}: dropped {delta:.1%}"
