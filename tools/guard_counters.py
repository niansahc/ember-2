"""
tools/guard_counters.py

Read the guard hit counters while the API is serving.

    python tools/guard_counters.py                 # every site
    python tools/guard_counters.py --dead          # evaluated, never fired
    python tools/guard_counters.py --unreached     # never evaluated at all
    python tools/guard_counters.py --reset         # start a new window

The distinction the --dead and --unreached views draw is the whole point
of the exercise. A site with evaluations > 0 and firings == 0 is a rule
that runs on every query and has never once been true on this corpus:
semantically dead configuration, invisible to coverage tooling because the
line executes. A site with evaluations == 0 was never reached at all,
which is ordinary dead code and a different repair.

Reads with a read-only connection, so it can run against a live database
without blocking or corrupting the writer.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.observability.guard_counters import (  # noqa: E402
    database_path,
    read_all,
    reset,
)


def _print_rows(rows: list[dict], title: str) -> None:
    print(f"  {title} ({len(rows)})")
    if not rows:
        print("    none")
        return
    print(f"    {'site':56} {'evaluations':>12} {'firings':>9} {'rate':>8}")
    for row in rows:
        denominator = row.get("denominator") or 0
        rate = f"{row['firings'] / denominator:8.4f}" if denominator else "       -"
        evaluations = row["evaluations"] if row["kind"] != "branch" else denominator
        print(
            f"    {row['site']:56} {evaluations:>12} {row['firings']:>9} {rate}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="counter database (default: the configured one)")
    parser.add_argument("--dead", action="store_true",
                        help="only sites evaluated at least once and never true")
    parser.add_argument("--unreached", action="store_true",
                        help="only sites never evaluated")
    parser.add_argument("--reset", action="store_true", help="clear every counter")
    args = parser.parse_args()

    path = Path(args.db) if args.db else database_path()

    if args.reset:
        reset(path)
        print(f"  counters cleared: {path}")
        return 0

    rows = read_all(path)
    print(f"  database: {path}")
    if not rows:
        print("  no counters recorded yet")
        return 0

    total_evaluations = sum(r["evaluations"] for r in rows if r["kind"] != "branch")
    print(f"  sites: {len(rows)} | total evaluations: {total_evaluations}")
    first = min((r["first_seen"] or "") for r in rows)
    last = max((r["last_seen"] or "") for r in rows)
    print(f"  window: {first} .. {last}")
    print()

    dead = [r for r in rows if (r.get("denominator") or 0) > 0 and r["firings"] == 0]
    unreached = [r for r in rows if (r.get("denominator") or 0) == 0]

    if args.dead:
        _print_rows(dead, "EVALUATED, NEVER FIRED -- semantically dead on this corpus")
        return 0
    if args.unreached:
        _print_rows(unreached, "NEVER EVALUATED -- not reached at all")
        return 0

    _print_rows(rows, "all sites")
    print()
    _print_rows(dead, "EVALUATED, NEVER FIRED -- semantically dead on this corpus")
    print()
    _print_rows(unreached, "NEVER EVALUATED -- not reached at all")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
