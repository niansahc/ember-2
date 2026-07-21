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

    rep = run_grounding_eval(
        driver,
        seed_fn=lambda: [{"query": "when is the deadline?", "text": "the deadline is Friday"}],
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
        driver, seed_fn=lambda: [{"query": "when?", "text": "the deadline is Friday"}],
        judge_claims=lambda r, ret: [{"claim": "Friday", "supported": True}],
        ratio_threshold=0.8)
    assert rep["cases"][0]["passed"] is True
    assert rep["metrics"]["supported_ratio"] == 1.0


def test_grounding_aborts_when_seed_not_retrievable_through_api():
    # The seeded record is NOT in what the API retrieved -> the API is serving a
    # different vault. The run must abort (vault_misaligned), not report garbage.
    driver = FakeDriver(response="whatever", retrieved=["totally unrelated record"])
    rep = run_grounding_eval(
        driver, seed_fn=lambda: [{"query": "q", "text": "the seeded fact about Atlas"}],
        judge_claims=lambda r, ret: [{"claim": "x", "supported": True}])
    assert rep["vault_misaligned"] is True
    assert rep["cases"] == []


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
    assert rep["judge_errors"] == 0
    assert SECRET not in json.dumps(rep)


class _FailingJudge:
    """Mimics ClaudeJudge's fail-closed fallback: score 1 + error marker."""

    def evaluate(self, response, rubric, context):
        return {"dimensions": {"directness": 1, "low_ceremony": 1,
                               "non_therapeutic": 1, "no_ai_cliche": 1},
                "flags": {}, "reasoning": {"score_parse_error": "Scoring call failed"}}


def test_register_flow_counts_judge_failures():
    # A run whose judge calls failed must be flagged, not silently recorded as a
    # legitimate (failing) baseline. One case x 3 runs = 3 failed judge calls.
    one_case = [{"case_id": "reg_1", "prompt": "hi", "failure_modes_probed": []}]
    rep = run_register_eval(_FailingJudge(), generate_fn=lambda p: "resp",
                            MultiRunResultCls=_FakeMultiRun, cases=one_case, runs=3)
    assert rep["judge_errors"] == 3


def test_grounding_flow_counts_judge_failures():
    from tests.eval.quality_judges import GROUNDING_ERROR_CLAIM
    driver = FakeDriver(response="a reply", retrieved=["some record text"])
    rep = run_grounding_eval(
        driver, seed_fn=lambda: [{"query": "q", "text": "some record text"}],
        judge_claims=lambda r, ret: [{"claim": GROUNDING_ERROR_CLAIM, "supported": False}])
    assert rep["judge_errors"] == 1


def test_drift_flow_counts_judge_failures():
    driver = FakeDriver(response="a reply")

    def failing_score(user, response):
        raise RuntimeError("judge unreachable")

    rep = run_drift_eval(driver, score_turn_fn=failing_score)
    # every turn's judge call failed
    assert rep["judge_errors"] == 20
    assert rep["total_calls"] == 20


def test_baseline_payload_stamps_provenance():
    from tests.eval.run_quality import _baseline_payload
    payload = _baseline_payload({"directness": 3.2, "pass_rate": 0.5},
                                "register", "claude-sonnet-4-5-20250929",
                                "2026-07-21T14:36:00+00:00")
    # metrics preserved flat (so the gate still reads them)
    assert payload["directness"] == 3.2
    assert payload["pass_rate"] == 0.5
    # provenance under _meta
    assert payload["_meta"]["eval"] == "register"
    assert payload["_meta"]["judge_model"] == "claude-sonnet-4-5-20250929"
    assert payload["_meta"]["generated_at"] == "2026-07-21T14:36:00+00:00"


def test_judge_outage_helper_distinguishes_blips_from_outages():
    from tests.eval.run_quality import _judge_outage
    assert _judge_outage({"judge_errors": 1, "total_calls": 9}) is False   # 11% - blip
    assert _judge_outage({"judge_errors": 3, "total_calls": 9}) is False   # 33% - tolerated
    assert _judge_outage({"judge_errors": 5, "total_calls": 9}) is True    # 56% - outage
    assert _judge_outage({"judge_errors": 0, "total_calls": 9}) is False
    assert _judge_outage({"judge_errors": 3, "total_calls": 0}) is False   # no data yet


class _FlakyJudge:
    """Fails on specific call indices, succeeds otherwise (mimics a transient
    judge blip within an otherwise-healthy run)."""

    def __init__(self, fail_on):
        self.n = 0
        self.fail_on = set(fail_on)

    def evaluate(self, response, rubric, context):
        i = self.n
        self.n += 1
        if i in self.fail_on:
            return {"dimensions": {"directness": 1, "low_ceremony": 1,
                                   "non_therapeutic": 1, "no_ai_cliche": 1},
                    "flags": {}, "reasoning": {"score_parse_error": "Not valid JSON: ..."}}
        return {"dimensions": {"directness": 4, "low_ceremony": 4,
                               "non_therapeutic": 4, "no_ai_cliche": 4},
                "flags": {}, "reasoning": {}}


def test_register_drops_transient_failure_and_aggregates_good_runs():
    # 1 of 3 runs fails; the failed (all-1) scores must NOT pollute the metrics -
    # the baseline comes from the 2 good runs, and the run is not an outage.
    from tests.eval.run_quality import _judge_outage
    one_case = [{"case_id": "reg_1", "prompt": "hi", "failure_modes_probed": []}]
    judge = _FlakyJudge(fail_on={0})  # first run fails, next two succeed
    rep = run_register_eval(judge, generate_fn=lambda p: "resp",
                            MultiRunResultCls=_FakeMultiRun, cases=one_case, runs=3)
    assert rep["judge_errors"] == 1
    assert rep["total_calls"] == 3
    # metrics reflect the 2 good runs (all 4s), not the dropped all-1 fallback
    assert rep["metrics"]["directness"] == 4
    assert _judge_outage(rep) is False
