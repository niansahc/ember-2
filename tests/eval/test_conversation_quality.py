import pytest
from tests.eval.harness import EmberEvalHarness, load_baseline_scores
from tests.eval.judge import ClaudeJudge
from tests.eval.golden_dataset import GOLDEN_CASES

pytestmark = pytest.mark.eval

harness = EmberEvalHarness(ollama_base_url="http://localhost:11434")
judge = ClaudeJudge(model="claude-haiku-4-5")

@pytest.fixture(scope="session")
def judge_client():
    return judge

@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["id"] for c in GOLDEN_CASES])
def test_golden_case(case, judge_client):
    response = harness.run_conversation(
        persona=case["persona"],
        vault_context=case["vault_context"],
        history=case["conversation_history"],
        user_message=case["user_message"]
    )
    scores = judge_client.evaluate(response=response, rubric=case["rubric"], context=case)
    for failure_mode in case["expected_failures_absent"]:
        assert not scores["flags"].get(failure_mode, False), \
            f"{case['id']}: {failure_mode} detected — {scores['reasoning'].get(failure_mode)}"
    for dimension, score in scores["dimensions"].items():
        assert score >= 3, \
            f"{case['id']}: {dimension} scored {score}/4 — {scores['reasoning'].get(dimension)}"

def test_no_regression_vs_baseline():
    current_scores = harness.run_full_suite(GOLDEN_CASES, judge)
    baseline = load_baseline_scores()
    for metric in current_scores:
        delta = baseline[metric] - current_scores[metric]
        assert delta <= 0.05, f"Regression on {metric}: dropped {delta:.1%}"
