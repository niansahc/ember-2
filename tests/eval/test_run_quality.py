"""
tests/eval/test_run_quality.py

Orchestration tests for run_quality with injected fakes (no Ollama, no Anthropic,
no live API). Verifies the seed -> drive -> judge -> aggregate -> report wiring
and the load-bearing privacy guarantee: response text never reaches the report.
"""

import json

from tests.eval.run_quality import (
    run_grounding_eval,
    run_drift_eval,
    run_register_eval,
)

SECRET = "SECRET_DIARY_TOKEN_do_not_leak"


class FakeDriver:
    def __init__(self, response, retrieved=None):
        self._response = response
        self._retrieved = retrieved or []

    def send_turn(self, message):
        return self._response, 0.5

    def fetch_retrieved_texts(self, message):
        return list(self._retrieved)


def test_grounding_flow_fails_on_confabulation_and_never_leaks_text():
    driver = FakeDriver(response=f"Your deadline is Monday. {SECRET}",
                        retrieved=["the deadline is Friday"])

    def judge_claims(response, retrieved):
        # Response claimed Monday; records say Friday -> unsupported.
        return [{"claim": "deadline is Monday", "supported": False}]

    rep = run_grounding_eval(driver, seed_fn=lambda: [{"query": "when is the deadline?"}],
                             judge_claims=judge_claims, ratio_threshold=0.8)
    assert rep["eval"] == "grounding"
    assert rep["cases"][0]["passed"] is False
    assert rep["metrics"]["supported_ratio"] == 0.0
    # Privacy: the response text (incl. the secret) must not be in the report.
    assert SECRET not in json.dumps(rep)


def test_grounding_flow_passes_when_grounded():
    driver = FakeDriver(response="Your deadline is Friday.",
                        retrieved=["the deadline is Friday"])
    rep = run_grounding_eval(
        driver, seed_fn=lambda: [{"query": "when?"}],
        judge_claims=lambda r, ret: [{"claim": "Friday", "supported": True}],
        ratio_threshold=0.8)
    assert rep["cases"][0]["passed"] is True
    assert rep["metrics"]["supported_ratio"] == 1.0


def test_drift_flow_fails_on_declining_scores():
    driver = FakeDriver(response=f"a reply {SECRET}")
    # Score declines across the 20-turn script: strong early, weak late.
    calls = {"n": 0}

    def score_turn(user, response):
        calls["n"] += 1
        val = 4.0 if calls["n"] <= 10 else 1.0
        return {"register": val, "honesty": val, "self_narrative": val}

    rep = run_drift_eval(driver, score_turn_fn=score_turn)
    assert rep["cases"][0]["passed"] is False
    assert rep["metrics"]["min_window_delta"] < 0
    assert SECRET not in json.dumps(rep)


def test_drift_flow_passes_when_stable():
    driver = FakeDriver(response="steady reply")
    rep = run_drift_eval(
        driver, score_turn_fn=lambda u, r: {"register": 3.0, "honesty": 3.0, "self_narrative": 3.0})
    assert rep["cases"][0]["passed"] is True


class _FakeMultiRun:
    def __init__(self, case_id, runs):
        self.case_id, self.runs, self._dims = case_id, runs, {}

    def add_run(self, result):
        for d, v in result["dimensions"].items():
            self._dims.setdefault(d, []).append(v)

    def dimension_averages(self):
        return {d: sum(v) / len(v) for d, v in self._dims.items()}

    def flag_fire_rates(self):
        return {}

    def passed(self, expected_absent):
        return all(a >= 3 for a in self.dimension_averages().values())


class _FakeJudge:
    def evaluate(self, response, rubric, context):
        return {"dimensions": {"directness": 4, "low_ceremony": 4,
                               "non_therapeutic": 4, "no_ai_cliche": 4},
                "flags": {}, "reasoning": {}}


def test_register_flow_aggregates_and_passes():
    rep = run_register_eval(_FakeJudge(), generate_fn=lambda p: f"resp {SECRET}",
                            MultiRunResultCls=_FakeMultiRun, runs=2)
    assert rep["eval"] == "register"
    assert rep["metrics"]["pass_rate"] == 1.0
    assert SECRET not in json.dumps(rep)
