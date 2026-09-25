"""
tools/cosine_spread.py

Measure the realized top-k cosine spread of the embedder over a corpus.

ADR-044 bounds the metadata prior against this quantity rather than
against taste: "a prior permitted to move a record further than the
entire observable similarity range is not a tiebreaker; it is the ranking
signal, with cosine as a tiebreaker." That bound is currently asserted
against 0.1504, a fixture-corpus figure, and the ADR records the
production number as never taken.

Definition, stated because the fixture figure's definition is not
reproducible from this repository and the two numbers are otherwise not
comparable:

    for each query q:
        take the top k results by RAW cosine, before any adjustment
        spread(q) = max(cosine) - min(cosine) over those k
    report mean, median and standard deviation of spread(q) over queries

Raw cosine, not the composed score: the bound is about what the embedder
can observe, and the composed score is the thing being bounded. Taking it
post-composition would compare the prior against itself.

The queries come from tools/traffic_window.py, which are synthetic,
authored in this repository, and already cover the policy cascade. They
are not drawn from anyone's vault.

Read-only. The retrieval-stats suppression from #228 is entered
explicitly rather than assumed, and the run refuses to proceed if the
environment has not taken effect. Prints counts and floats. Never prints
a query result, a record, or an id.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_K = 8


def measure(queries: list[str], k: int) -> dict:
    from src.retrieval.retrieval_stats import (
        retrieval_stats_disabled,
        retrieval_stats_disabled_now,
    )
    from src.retrieval.semantic_search import semantic_search
    from src.retrieval.embed_memory import embed_text

    spreads: list[float] = []
    tops: list[float] = []
    bottoms: list[float] = []
    short = 0

    with retrieval_stats_disabled():
        if not retrieval_stats_disabled_now():
            raise RuntimeError(
                "retrieval-stat suppression did not take effect; refusing to "
                "search the corpus with stats live"
            )
        for query in queries:
            embedding = embed_text(query)
            results = semantic_search(query, limit=k, query_embedding=embedding)
            # raw_score is the cosine before any additive or multiplicative
            # adjustment; score is not, and is the quantity under test.
            cosines = sorted(
                (float(r.get("raw_score", 0.0)) for r in results), reverse=True
            )[:k]
            if len(cosines) < k:
                short += 1
            if len(cosines) < 2:
                continue
            spreads.append(cosines[0] - cosines[-1])
            tops.append(cosines[0])
            bottoms.append(cosines[-1])

    if not spreads:
        raise RuntimeError("no query returned enough results to measure a spread")

    return {
        "k": k,
        "queries": len(queries),
        "measured": len(spreads),
        "short_of_k": short,
        "mean_spread": statistics.fmean(spreads),
        "median_spread": statistics.median(spreads),
        "stdev_spread": statistics.stdev(spreads) if len(spreads) > 1 else 0.0,
        "min_spread": min(spreads),
        "max_spread": max(spreads),
        "mean_top1": statistics.fmean(tops),
        "mean_topk": statistics.fmean(bottoms),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-k", type=int, default=DEFAULT_K,
                        help=f"rank depth for the spread (default {DEFAULT_K})")
    parser.add_argument("--reference", type=float, default=0.1504,
                        help="figure to compare against (default: ADR-044's "
                             "fixture-corpus 0.1504)")
    args = parser.parse_args()

    from traffic_window import QUERY_SET

    queries = [entry["query"] for entry in QUERY_SET]
    result = measure(queries, args.k)

    print(f"  queries            : {result['queries']} "
          f"(measured {result['measured']}, short of k {result['short_of_k']})")
    print(f"  k                  : {result['k']}")
    print(f"  mean top-{result['k']} spread : {result['mean_spread']:.4f}")
    print(f"  median             : {result['median_spread']:.4f}")
    print(f"  stdev              : {result['stdev_spread']:.4f}")
    print(f"  range              : {result['min_spread']:.4f} .. {result['max_spread']:.4f}")
    print(f"  mean rank-1 cosine : {result['mean_top1']:.4f}")
    print(f"  mean rank-{result['k']} cosine : {result['mean_topk']:.4f}")
    print()
    print(f"  reference          : {args.reference:.4f}")
    ratio = result["mean_spread"] / args.reference if args.reference else float("nan")
    print(f"  measured / reference: {ratio:.2f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
