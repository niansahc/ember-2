"""
tests/eval/test_eval_grounding.py

Unit tests for the grounding-fidelity aggregation math. Pure functions - runs in
the default Tier-1 suite (no live retrieval, no judge call here).

Grounding fidelity judges each factual claim in Ember's response as supported or
unsupported by the records that retrieval ACTUALLY surfaced (fetched via
/debug-context). The verdict is the supported-claim ratio + a confabulation flag.
"""

from tests.eval.eval_grounding import grounding_verdict


def test_all_supported_passes():
    claims = [
        {"claim": "the deadline is Friday", "supported": True},
        {"claim": "the report is quarterly", "supported": True},
    ]
    v = grounding_verdict(claims, ratio_threshold=0.8)
    assert v["supported_ratio"] == 1.0
    assert v["confabulated_count"] == 0
    assert v["passed"] is True


def test_confabulation_fails():
    claims = [
        {"claim": "the deadline is Friday", "supported": True},
        {"claim": "your manager is named Sarah", "supported": False},
        {"claim": "you live in Boston", "supported": False},
    ]
    v = grounding_verdict(claims, ratio_threshold=0.8)
    assert round(v["supported_ratio"], 3) == round(1 / 3, 3)
    assert v["confabulated_count"] == 2
    assert v["passed"] is False


def test_empty_claims_is_vacuously_grounded():
    # A response with no factual claims (e.g. a clarifying question) cannot
    # confabulate - it must not be scored as a grounding failure.
    v = grounding_verdict([], ratio_threshold=0.8)
    assert v["passed"] is True
    assert v["supported_ratio"] == 1.0
    assert v["confabulated_count"] == 0


def test_threshold_boundary_is_inclusive():
    claims = [{"claim": "x", "supported": True}] * 4 + [{"claim": "y", "supported": False}]
    v = grounding_verdict(claims, ratio_threshold=0.8)  # 4/5 == 0.8
    assert v["supported_ratio"] == 0.8
    assert v["passed"] is True
