"""
tests/eval/test_eval_drift.py

Unit tests for the multi-turn drift aggregation math (ADR: response-quality
eval framework). Pure functions - no Ollama, no Claude, no live API - so these
run in the default Tier-1 suite (no `eval` marker) and stay CI-safe (the module
imports anthropic lazily).

Drift verdict: gate on the first-window vs last-window delta per dimension;
report the linear slope as a diagnostic.
"""

from tests.eval.eval_drift import window_delta, trend_slope, drift_verdict


def test_window_delta_measures_last_minus_first():
    # 20 turns: strong (4) early, degraded (2) in the last window.
    scores = [4.0] * 15 + [2.0] * 5
    # first-window (turns 1-5) mean = 4.0; last-window (turns 16-20) mean = 2.0
    assert window_delta(scores, window=5) == -2.0


def test_window_delta_zero_when_stable():
    scores = [3.0] * 20
    assert window_delta(scores, window=5) == 0.0


def test_window_delta_positive_when_improving():
    scores = [2.0] * 5 + [3.0] * 10 + [4.0] * 5
    assert window_delta(scores, window=5) == 2.0


def test_trend_slope_negative_for_decline():
    scores = [4.0, 3.5, 3.0, 2.5, 2.0]
    # perfectly linear decline of 0.5 per turn
    assert round(trend_slope(scores), 3) == -0.5


def test_trend_slope_zero_for_flat():
    assert trend_slope([3.0] * 10) == 0.0


def test_drift_verdict_fails_on_degrading_dimension():
    per_dim = {
        "register": [4.0] * 15 + [1.5] * 5,      # delta = -2.5, degrades
        "honesty": [3.0] * 20,                    # stable
        "self_narrative": [3.0] * 20,             # stable
    }
    verdict = drift_verdict(per_dim, threshold=0.5, window=5)
    assert verdict["passed"] is False
    assert verdict["dimensions"]["register"]["passed"] is False
    assert verdict["dimensions"]["honesty"]["passed"] is True
    # slope is reported as a diagnostic on every dimension
    assert "slope" in verdict["dimensions"]["register"]


def test_drift_verdict_passes_when_all_stable():
    per_dim = {d: [3.0] * 20 for d in ("register", "honesty", "self_narrative")}
    verdict = drift_verdict(per_dim, threshold=0.5, window=5)
    assert verdict["passed"] is True


def test_drift_verdict_tolerates_single_noisy_turn():
    # One bad turn inside an otherwise-stable run must NOT fail the case -
    # the window mean absorbs it (this is why we gate on windows, not floors).
    per_dim = {"register": [3.0] * 10 + [1.0] + [3.0] * 9}
    verdict = drift_verdict(per_dim, threshold=0.5, window=5)
    assert verdict["passed"] is True
