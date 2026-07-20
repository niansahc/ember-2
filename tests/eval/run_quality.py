"""
tests/eval/run_quality.py

Local release-gate orchestrator for the response-quality eval framework.

    python -m tests.eval.run_quality --evals register,grounding,drift

Runs the requested evals, writes a METADATA-ONLY report, and compares each eval's
scalar metrics to its baseline (a >max-drop regression fails the gate). This runs
ONLY where Ollama + a live Ember API exist (dev box / self-hosted runner) - it is
not part of GitHub CI (which stays `-m "not eval"`). Run it OUTSIDE Claude Code:
it produces vault-grounded response text that must not enter a session log
(CLAUDE.md); the report it writes carries no response text.

The per-eval run_* functions take injected dependencies (driver, judge, generate)
so the orchestration is unit-testable with fakes without Ollama/Anthropic. main()
wires the real dependencies lazily.
"""

from __future__ import annotations

import argparse
import statistics
import sys

from tests.eval.eval_grounding import grounding_verdict
from tests.eval.eval_drift import drift_verdict, DRIFT_DIMENSIONS
from tests.eval.quality_report import build_case_report, compare_to_baseline
from tests.eval.quality_cases import DRIFT_SCRIPT, REGISTER_CASES, REGISTER_RUBRIC


def _mean(xs) -> float:
    xs = list(xs)
    return statistics.mean(xs) if xs else 0.0


def run_grounding_eval(driver, seed_fn, judge_claims, ratio_threshold=0.8) -> dict:
    """Seed a synthetic corpus, drive each query live, judge response claims
    against the ACTUALLY retrieved records."""
    corpus = seed_fn()
    cases, ratios, passes = [], [], []
    for rec in corpus:
        query = rec["query"]
        response, latency = driver.send_turn(query)
        retrieved = driver.fetch_retrieved_texts(query)
        verdict = grounding_verdict(judge_claims(response, retrieved), ratio_threshold)
        cases.append(build_case_report(
            case_id=f"grounding::{query[:40]}", eval_name="grounding",
            passed=verdict["passed"],
            metrics={"supported_ratio": verdict["supported_ratio"],
                     "confabulated_count": verdict["confabulated_count"],
                     "total_claims": verdict["total_claims"]},
            latency=latency, word_count=len(response.split()),
        ))
        ratios.append(verdict["supported_ratio"])
        passes.append(1.0 if verdict["passed"] else 0.0)
    return {"eval": "grounding", "cases": cases,
            "metrics": {"supported_ratio": _mean(ratios), "pass_rate": _mean(passes)}}


def run_drift_eval(driver, score_turn_fn, script=DRIFT_SCRIPT,
                   threshold=0.5, window=5) -> dict:
    """Drive the canned 20-turn script live; score each turn; gate on window delta."""
    per_dim = {d: [] for d in DRIFT_DIMENSIONS}
    total_latency = 0.0
    for turn in script:
        response, latency = driver.send_turn(turn)
        total_latency += latency
        scores = score_turn_fn(turn, response)
        for d in DRIFT_DIMENSIONS:
            per_dim[d].append(scores[d])
    verdict = drift_verdict(per_dim, threshold=threshold, window=window)
    metrics = {}
    for d in DRIFT_DIMENSIONS:
        metrics[f"{d}_delta"] = verdict["dimensions"][d]["delta"]
        metrics[f"{d}_slope"] = verdict["dimensions"][d]["slope"]
    case = build_case_report(case_id="drift::20turn", eval_name="drift",
                             passed=verdict["passed"], metrics=metrics,
                             latency=total_latency)
    # Gate metric: the worst (most negative) window delta across dimensions.
    metrics_summary = {"min_window_delta": min(
        verdict["dimensions"][d]["delta"] for d in DRIFT_DIMENSIONS)}
    return {"eval": "drift", "cases": [case], "metrics": metrics_summary}


def run_register_eval(judge, generate_fn, MultiRunResultCls,
                      cases=REGISTER_CASES, runs=3) -> dict:
    """Generate register-pressure responses (raw Ollama) and score voice with the
    Sonnet judge + multi-run fire-rate thresholds."""
    case_reports, pass_flags = [], []
    for case in cases:
        mr = MultiRunResultCls(case["case_id"], runs)
        for _ in range(runs):
            response = generate_fn(case["prompt"])
            result = judge.evaluate(response, REGISTER_RUBRIC, {
                "user_message": case["prompt"],
                "failure_modes_probed": case["failure_modes_probed"],
            })
            mr.add_run(result)
        passed = mr.passed(case["failure_modes_probed"])
        pass_flags.append(1.0 if passed else 0.0)
        case_reports.append(build_case_report(
            case_id=case["case_id"], eval_name="register", passed=passed,
            metrics=mr.dimension_averages(), flags=mr.flag_fire_rates(),
        ))
    avg_dims = {}
    for cr in case_reports:
        for k, v in cr["metrics"].items():
            avg_dims.setdefault(k, []).append(v)
    metrics = {k: _mean(v) for k, v in avg_dims.items()}
    metrics["pass_rate"] = _mean(pass_flags)
    return {"eval": "register", "cases": case_reports, "metrics": metrics}


def _gate(report: dict, baseline: dict, max_drop: float) -> dict:
    """Attach a baseline-regression verdict to an eval report."""
    report["baseline_check"] = compare_to_baseline(report["metrics"], baseline, max_drop)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Response-quality eval release gate")
    parser.add_argument("--evals", default="register,grounding,drift",
                        help="comma list: register,grounding,drift")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=3, help="register multi-run count")
    parser.add_argument("--max-drop", type=float, default=0.05)
    parser.add_argument("--report", default="logs/eval_quality/latest.json")
    parser.add_argument("--baseline-dir", default="tests/eval")
    parser.add_argument("--update-baseline", action="store_true",
                        help="write current metrics as the new baseline (calibration)")
    args = parser.parse_args(argv)

    requested = [e.strip() for e in args.evals.split(",") if e.strip()]

    # Lazy live wiring - only imported when actually running the gate.
    import os
    from tests.eval.seeder import seed_corpus
    from tests.eval.judge import ClaudeJudge
    from tests.eval.harness import MultiRunResult
    from tests.eval.live_driver import EmberLiveDriver
    from tests.eval.quality_judges import (
        score_claims, score_turn, GROUNDING_JUDGE_MODEL,
    )
    from tests.eval.quality_report import write_report, load_baseline

    api_key = os.environ.get("EMBER_API_KEY")
    driver = EmberLiveDriver(base_url=args.base_url, api_key=api_key)

    def _ollama_generate(prompt: str) -> str:
        import requests
        r = requests.post("http://127.0.0.1:11434/api/chat", json={
            "model": os.environ.get("EMBER_MODEL", "qwen3:8b"),
            "messages": [{"role": "user", "content": prompt}], "stream": False,
        }, timeout=120)
        r.raise_for_status()
        return r.json()["message"]["content"]

    reports, overall_ok = [], True
    for name in requested:
        if name == "grounding":
            rep = run_grounding_eval(driver, seed_corpus, score_claims)
        elif name == "drift":
            rep = run_drift_eval(driver, score_turn)
        elif name == "register":
            rep = run_register_eval(ClaudeJudge(model=GROUNDING_JUDGE_MODEL),
                                    _ollama_generate, MultiRunResult, runs=args.runs)
        else:
            print(f"[eval] unknown eval '{name}', skipping")
            continue

        baseline_path = f"{args.baseline_dir}/baseline_quality_{name}.json"
        baseline = load_baseline(baseline_path)
        _gate(rep, baseline, args.max_drop)
        if args.update_baseline:
            write_report(baseline_path, rep["metrics"])
            print(f"[eval] {name}: baseline updated (calibration)")
        else:
            bc = rep["baseline_check"]
            status = "CALIBRATION" if bc["calibration"] else ("PASS" if bc["passed"] else "FAIL")
            print(f"[eval] {name}: {status}  metrics={rep['metrics']}")
            if not bc["passed"]:
                overall_ok = False
        reports.append(rep)

    write_report(args.report, {"reports": reports})
    print(f"[eval] metadata report written to {args.report}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
