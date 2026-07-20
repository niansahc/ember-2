"""
tests/eval/eval_grounding.py

Grounding-fidelity eval (response-quality eval framework).

Tests whether Ember stays grounded in the records retrieval ACTUALLY surfaced,
rather than confabulating. A synthetic corpus is seeded into a test vault
(seeder.py); a query runs through the live Ember pipeline; the packet retrieval
produced is fetched via /debug-context; a Sonnet judge decomposes the response
into factual claims and marks each supported / unsupported by the RETRIEVED
records. The verdict is the supported-claim ratio plus a confabulation flag.

The aggregation below is pure so it runs in the default Tier-1 suite; the judge
call (score_claims, added alongside) imports anthropic lazily.
"""

from __future__ import annotations

# A response passes grounding when at least this fraction of its factual claims
# are supported by the retrieved records.
DEFAULT_RATIO_THRESHOLD = 0.8


def grounding_verdict(
    claim_verdicts: list[dict],
    ratio_threshold: float = DEFAULT_RATIO_THRESHOLD,
) -> dict:
    """Aggregate per-claim supported/unsupported judgments into a verdict.

    `claim_verdicts` is a list of {"claim": str, "supported": bool}. A response
    with no factual claims is vacuously grounded (ratio 1.0) - it cannot
    confabulate. Otherwise the ratio is supported / total, and the case passes
    when the ratio meets the threshold.
    """
    total = len(claim_verdicts)
    if total == 0:
        return {
            "supported_ratio": 1.0,
            "total_claims": 0,
            "supported_count": 0,
            "confabulated_count": 0,
            "passed": True,
        }
    supported = sum(1 for c in claim_verdicts if c.get("supported"))
    confabulated = total - supported
    ratio = supported / total
    return {
        "supported_ratio": ratio,
        "total_claims": total,
        "supported_count": supported,
        "confabulated_count": confabulated,
        "passed": ratio >= ratio_threshold,
    }
