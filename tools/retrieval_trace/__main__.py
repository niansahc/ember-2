"""
tools/retrieval_trace/__main__.py

    python -m tools.retrieval_trace capture [--out PATH] [--no-content]
                                            [--pool-limit N]
    python -m tools.retrieval_trace verify   PATH
    python -m tools.retrieval_trace coverage PATH
    python -m tools.retrieval_trace morris   PATH [--trajectories R] [--levels P]
                                                  [--seed N] [--json OUT]
    python -m tools.retrieval_trace sweep    PATH PARAM V1,V2,...

Run `coverage` before any sensitivity pass. It reports which parameters the
captured corpus can say anything about at all; the rest score a Sobol index
of zero for want of an activation, which is not the same finding and will
be read as if it were.

Capture is read-only (#206, #228) and prints the database digest either
side. It prints counts, policies and digests -- never a query, never a
record, never a score attached to either. The trace file is where the data
goes, and the trace file lives outside the repository.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.retrieval_trace.capture import capture_run, default_output_path  # noqa: E402
from tools.retrieval_trace.morris import (  # noqa: E402
    ENDPOINTS,
    format_report,
    rank_stability,
    screen,
    to_dict,
)
from tools.retrieval_trace.queries import EXPECTED_POLICIES, capture_pairs  # noqa: E402
from tools.retrieval_trace.replay import (  # noqa: E402
    check_fidelity,
    parameter_coverage,
    sweep,
)
from tools.retrieval_trace.schema import load_run  # noqa: E402


def _report(run) -> int:
    print(f"  queries captured : {len(run.queries)}")
    print(f"  candidates       : {sum(len(q.candidates) for q in run.queries)}")
    print(f"  vault fingerprint: {run.vault_fingerprint}")
    print(f"  pool limit       : {run.pool_limit}{' (widened)' if run.pool_widened else ''}")

    covered = run.policies_covered()
    print(f"  policies covered : {len(covered & EXPECTED_POLICIES)}/{len(EXPECTED_POLICIES)}")
    for policy in sorted(covered):
        count = sum(1 for q in run.queries if q.policy_name == policy)
        marker = "" if policy in EXPECTED_POLICIES else "  (not a vault policy)"
        print(f"    {policy:16} {count:>3} query(s){marker}")
    missing = EXPECTED_POLICIES - covered
    if missing:
        print(f"  MISSING policies : {sorted(missing)}")

    print(f"  digest before    : {run.db_digest_before}")
    print(f"  digest after     : {run.db_digest_after}")
    print(f"  digest unchanged : {run.digest_unchanged}")

    report = check_fidelity(run)
    print(f"  replay fidelity  : {report.summary()}")
    for line in (report.score_mismatches + report.delivery_mismatches)[:10]:
        print(f"    {line}")

    ok = run.digest_unchanged and report.exact and not missing
    print(f"  RESULT           : {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(prog="tools.retrieval_trace")
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="capture traces over the query set")
    cap.add_argument("--out", help="output path (must be outside the repository)")
    cap.add_argument(
        "--no-content",
        action="store_true",
        help="omit candidate content. Scores still replay exactly, but "
        "delivery replay on diversity policies cannot, because "
        "_select_diverse_memory reads content.",
    )
    cap.add_argument(
        "--pool-limit",
        type=int,
        help="widen the candidate pool beyond the shipped 8. The delivered "
        "set in such a trace is the widened pipeline's, not production's.",
    )

    ver = sub.add_parser("verify", help="replay a trace at defaults and compare")
    ver.add_argument("path")

    cov = sub.add_parser(
        "coverage", help="which parameters this trace can say anything about"
    )
    cov.add_argument("path")

    mor = sub.add_parser("morris", help="Morris screening: rank parameters by mu*")
    mor.add_argument("path")
    mor.add_argument("--trajectories", type=int, default=10)
    mor.add_argument("--levels", type=int, default=8, help="grid levels, must be even")
    mor.add_argument("--seed", type=int, default=20260923)
    mor.add_argument("--top", type=int, default=0, help="limit each table (0 = all)")
    mor.add_argument("--json", help="also write the full results as JSON")
    mor.add_argument(
        "--check-stability",
        type=int,
        default=0,
        metavar="N",
        help="re-run with N further seeds and report whether the top of the "
        "ranking survives a different draw of trajectories",
    )

    swp = sub.add_parser("sweep", help="one-at-a-time sweep of one parameter")
    swp.add_argument("path")
    swp.add_argument("param")
    swp.add_argument("values", help="comma-separated")

    args = parser.parse_args()

    if args.command == "capture":
        out = Path(args.out) if args.out else default_output_path()

        def progress(index, total, query_id):
            print(f"  [{index}/{total}] {query_id}", flush=True)

        run = capture_run(
            capture_pairs(),
            include_content=not args.no_content,
            pool_limit=args.pool_limit,
            progress=progress,
        )
        written = run.write(out)
        print()
        print(f"  trace written    : {written}")
        return _report(run)

    if args.command == "verify":
        return _report(load_run(Path(args.path)))

    if args.command == "coverage":
        run = load_run(Path(args.path))
        results = parameter_coverage(run)
        inert = [name for name, moved in results if moved == 0]
        print(f"  parameters        : {len(results)}")
        print(f"  exercised         : {len(results) - len(inert)}")
        print(f"  inert (index 0 is uninformative, not a finding): {len(inert)}")
        for name in inert:
            print(f"    inert  {name}")
        print("  strongest:")
        for name, moved in sorted(results, key=lambda p: -p[1])[:10]:
            print(f"    {name:36} {moved:>5} candidate(s)")
        return 0

    if args.command == "morris":
        run = load_run(Path(args.path))

        def morris_progress(done, total, evaluations):
            print(
                f"  trajectory {done}/{total} ({evaluations} evaluations)",
                flush=True,
            )

        screening = screen(
            run,
            trajectories=args.trajectories,
            levels=args.levels,
            seed=args.seed,
            progress=morris_progress,
        )
        print()
        print(format_report(screening, top=args.top))

        if args.check_stability:
            extra = [
                screen(
                    run,
                    trajectories=args.trajectories,
                    levels=args.levels,
                    seed=args.seed + offset,
                )
                for offset in range(1, args.check_stability + 1)
            ]
            print()
            print("  STABILITY (does the ranking survive a different draw?)")
            for endpoint in ENDPOINTS:
                report = rank_stability([screening] + extra, endpoint, top=10)
                verdict = "stable" if report.stable() else "UNSTABLE -- raise --trajectories"
                print(
                    f"    {endpoint:9} top-10 agreement {report.agreement:.2f} "
                    f"across {len(report.seeds)} seed(s); worst rank movement "
                    f"{report.max_displacement} place(s): {verdict}"
                )

        if args.json:
            # Parameter names and numbers only -- no vault data -- so unlike
            # a trace this may be written anywhere the caller wants it.
            out = Path(args.json)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(to_dict(screening), indent=2), encoding="utf-8")
            print()
            print(f"  results written  : {out}")
        return 0

    if args.command == "sweep":
        run = load_run(Path(args.path))
        values = [float(v) for v in args.values.split(",")]
        print(f"  parameter: {args.param}")
        for value, changed in sweep(run, args.param, values):
            print(f"    {value:>10.4f} -> {changed:>4} delivered slot change(s)")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
