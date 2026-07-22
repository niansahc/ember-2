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
import logging
import statistics
import sys

from tests.eval.eval_grounding import grounding_verdict
from tests.eval.eval_drift import drift_verdict, DRIFT_DIMENSIONS
from tests.eval.quality_report import build_case_report, compare_to_baseline
from tests.eval.quality_cases import DRIFT_SCRIPT, REGISTER_CASES, REGISTER_RUBRIC
from tests.eval.quality_judges import GROUNDING_ERROR_CLAIM, NEUTRAL_TURN_SCORE

logger = logging.getLogger("ember.eval_quality")

# reasoning keys ClaudeJudge emits when a judge call fails (see judge.py).
_JUDGE_ERROR_MARKERS = ("score_parse_error", "flag_parse_error")

# A few judge calls will occasionally fail transiently (a rare API blip or a
# malformed judge response that retry can't recover). Dropping those calls and
# aggregating the rest is fine; only a HIGH failure rate means a real outage
# (bad key, unreachable model) where the whole run is untrustworthy. Above this
# fraction the run aborts instead of writing a baseline.
MAX_JUDGE_ERROR_RATE = 0.34

# Only the stable 0-1 aggregate metrics gate a run. The 1-4 per-dimension scores
# are kept in the baseline as diagnostics but are too noisy (judge variance ~0.4
# run-to-run) to gate on a tight threshold. Drift gates on its window-delta.
GATE_METRICS = {
    "register": ["pass_rate"],
    "grounding": ["supported_ratio", "pass_rate"],
    "drift": ["min_window_delta"],
}


def _mean(xs) -> float:
    xs = list(xs)
    return statistics.mean(xs) if xs else 0.0


def _judge_outage(rep: dict, max_rate: float = MAX_JUDGE_ERROR_RATE) -> bool:
    """True when an eval's judge-failure rate is high enough to distrust the whole
    run (a real outage), vs a tolerable handful of transient drops."""
    total = rep.get("total_calls", 0)
    if not total:
        return False
    return (rep.get("judge_errors", 0) / total) > max_rate


def _baseline_payload(metrics: dict, eval_name: str, judge_model: str,
                      generated_at: str) -> dict:
    """Wrap baseline metrics with provenance so each committed baseline version is
    self-describing in git history (which model/judge/date produced it). The
    `_meta` key is ignored by compare_to_baseline, so it does not affect gating.
    """
    payload = dict(metrics)
    payload["_meta"] = {
        "eval": eval_name,
        "judge_model": judge_model,
        "generated_at": generated_at,
    }
    return payload


def _reasoning_has_judge_error(reasoning) -> bool:
    """True if a judge result's reasoning carries a fail-closed error marker."""
    if not isinstance(reasoning, dict):
        return False
    for key, val in reasoning.items():
        if key in _JUDGE_ERROR_MARKERS:
            return True
        if isinstance(val, str) and "failed" in val.lower():
            return True
    return False


def _seed_visible(rec: dict, retrieved_texts: list) -> bool:
    """True if a seeded record is actually retrievable through the live API.

    Records are stored verbatim, so a distinctive chunk of the seed text should
    appear in what retrieval returned. Used to catch vault misalignment (the API
    serving a different vault than the one the corpus was seeded into).
    """
    needle = rec["text"][:40].strip().lower()
    return any(needle in (t or "").lower() for t in retrieved_texts)


def run_grounding_eval(driver, seed_fn, judge_claims, ratio_threshold=0.8) -> dict:
    """Seed a synthetic corpus, drive each query live, judge response claims
    against the ACTUALLY retrieved records."""
    driver.new_session("sess_grounding")  # fresh session per run
    corpus = seed_fn()

    # Vault-alignment guard. The corpus is seeded into THIS process's vault, but
    # the live API retrieves from ITS vault. If they differ (API not pointed at
    # the seeded test vault), grounding would silently test the wrong corpus - or
    # the real vault. Verify a seeded record is retrievable through the API and
    # abort loudly if not, instead of reporting garbage.
    if corpus:
        probe = corpus[0]
        if not _seed_visible(probe, driver.fetch_retrieved_texts(probe["query"])):
            return {"eval": "grounding", "cases": [], "judge_errors": 0,
                    "total_calls": 0, "metrics": {}, "vault_misaligned": True}

    cases, ratios, passes = [], [], []
    judge_errors = 0
    total_calls = 0
    for rec in corpus:
        total_calls += 1
        query = rec["query"]
        response, latency = driver.send_turn(query)
        retrieved = driver.fetch_retrieved_texts(query)
        claim_verdicts = judge_claims(response, retrieved)
        if any(c.get("claim") == GROUNDING_ERROR_CLAIM for c in claim_verdicts):
            judge_errors += 1
            logger.warning("[eval] grounding judge error on query %r "
                           "(dropped from aggregate)", query[:40])
            continue  # drop: a broken judge is not a real confabulation
        verdict = grounding_verdict(claim_verdicts, ratio_threshold)
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
    return {"eval": "grounding", "cases": cases, "judge_errors": judge_errors,
            "total_calls": total_calls,
            "metrics": {"supported_ratio": _mean(ratios), "pass_rate": _mean(passes)}}


def run_drift_eval(driver, score_turn_fn, script=DRIFT_SCRIPT,
                   threshold=0.5, window=5) -> dict:
    """Drive the canned 20-turn script live; score each turn; gate on window delta."""
    driver.new_session("sess_drift")  # isolate this run's conversation from prior runs
    per_dim = {d: [] for d in DRIFT_DIMENSIONS}
    total_latency = 0.0
    judge_errors = 0
    for turn in script:
        response, latency = driver.send_turn(turn)
        total_latency += latency
        try:
            scores = score_turn_fn(turn, response)
        except Exception as exc:  # noqa: BLE001
            # Transient judge failure on a turn. Count it and use a neutral
            # placeholder (a single constant barely moves the window delta, and
            # keeps the turn sequence intact). A HIGH failure rate still aborts
            # the run at the caller's outage guard.
            judge_errors += 1
            logger.warning("[eval] drift judge error on a turn "
                           "(using neutral placeholder): %s", exc)
            scores = {d: NEUTRAL_TURN_SCORE for d in DRIFT_DIMENSIONS}
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
    return {"eval": "drift", "cases": [case], "judge_errors": judge_errors,
            "total_calls": len(script), "metrics": metrics_summary}


def run_register_eval(judge, generate_fn, MultiRunResultCls,
                      cases=REGISTER_CASES, runs=5) -> dict:
    """Generate register-pressure responses (raw Ollama) and score voice with the
    Sonnet judge + multi-run fire-rate thresholds."""
    case_reports, pass_flags = [], []
    judge_errors = 0
    total_calls = 0
    for case in cases:
        successes = []
        for _ in range(runs):
            total_calls += 1
            response = generate_fn(case["prompt"])
            result = judge.evaluate(response, REGISTER_RUBRIC, {
                "user_message": case["prompt"],
                "failure_modes_probed": case["failure_modes_probed"],
            })
            if _reasoning_has_judge_error(result.get("reasoning", {})):
                judge_errors += 1
                logger.warning("[eval] register judge error on case %s "
                               "(dropped from aggregate)", case["case_id"])
                continue  # drop the fallback scores so they do not skew the baseline
            successes.append(result)
        if not successes:
            continue  # every run for this case failed - no valid data to aggregate
        mr = MultiRunResultCls(case["case_id"], len(successes))
        for result in successes:
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
    return {"eval": "register", "cases": case_reports, "judge_errors": judge_errors,
            "total_calls": total_calls, "metrics": metrics}


def _gate(report: dict, baseline: dict, max_drop: float) -> dict:
    """Attach a baseline-regression verdict to an eval report.

    Gates only on the eval's stable aggregate metrics (GATE_METRICS); the noisy
    per-dimension scores are diagnostic and do not gate.
    """
    report["baseline_check"] = compare_to_baseline(
        report["metrics"], baseline, max_drop,
        metrics_to_check=GATE_METRICS.get(report["eval"]),
    )
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Response-quality eval release gate")
    parser.add_argument("--evals", default="register,grounding,drift",
                        help="comma list: register,grounding,drift")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--runs", type=int, default=5, help="register multi-run count")
    parser.add_argument("--max-drop", type=float, default=0.05)
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="per-turn live-API timeout in seconds (grounded "
                             "generation is slow)")
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
        score_claims, score_turn, sonnet_judge_model, haiku_judge_model,
    )
    from tests.eval.quality_report import write_report, load_baseline

    api_key = os.environ.get("EMBER_API_KEY")
    driver = EmberLiveDriver(base_url=args.base_url, api_key=api_key, timeout=args.timeout)

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
            rep = run_register_eval(ClaudeJudge(model=sonnet_judge_model()),
                                    _ollama_generate, MultiRunResult, runs=args.runs)
        else:
            print(f"[eval] unknown eval '{name}', skipping")
            continue

        # Vault-alignment guard (grounding): the seeded corpus was not retrievable
        # through the API, so it is serving a different vault. Abort loudly rather
        # than report garbage or touch the real vault.
        if rep.get("vault_misaligned"):
            print(f"[eval] {name}: VAULT MISALIGNED - the seeded corpus is not "
                  "retrievable through the API, so it is not serving the seeded "
                  "test vault. Point the API at PRIVATE_VAULT_PATH="
                  "<test vault> (the same path this eval seeds into) and re-run. "
                  "NOT writing a baseline.")
            overall_ok = False
            reports.append(rep)
            continue

        # Judge-health guard. A handful of judge calls fail transiently; those
        # are dropped from the aggregate inside the run_* functions, so the
        # metrics are already clean. Only a HIGH failure rate means a real outage
        # (bad key, unreachable model) where the whole run is untrustworthy - then
        # refuse to write a baseline or pass the gate.
        je = rep.get("judge_errors", 0)
        total = rep.get("total_calls", 0)
        if _judge_outage(rep):
            print(f"[eval] {name}: JUDGE OUTAGE - {je}/{total} judge calls failed "
                  f"(> {MAX_JUDGE_ERROR_RATE:.0%}); results are invalid (check the "
                  "ANTHROPIC key and that the judge model id is accessible). NOT "
                  "writing a baseline and NOT passing the gate.")
            overall_ok = False
            reports.append(rep)
            continue
        if je:
            print(f"[eval] {name}: {je}/{total} judge call(s) failed transiently and "
                  "were dropped from the aggregate; proceeding on the rest.")

        baseline_path = f"{args.baseline_dir}/baseline_quality_{name}.json"
        baseline = load_baseline(baseline_path)
        _gate(rep, baseline, args.max_drop)
        if args.update_baseline:
            from datetime import datetime, timezone
            judge_model = haiku_judge_model() if name == "drift" else sonnet_judge_model()
            payload = _baseline_payload(
                rep["metrics"], name, judge_model,
                datetime.now(timezone.utc).isoformat(),
            )
            write_report(baseline_path, payload)
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
