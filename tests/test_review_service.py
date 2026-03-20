import json

from src.safety.models import SafetyReviewContext
from src.safety.review_service import ResponseReviewService


def test_review_allows_safe_draft_when_llm_returns_no_issues() -> None:
    def fake_llm(_: str) -> str:
        return json.dumps(
            {
                "issues_found": [],
                "severity": "none",
                "suggested_changes": [],
                "triggered_rules": [],
            }
        )

    service = ResponseReviewService(llm_callable=fake_llm)

    context = SafetyReviewContext(
        user_message="Explain how solar panels work.",
        draft_response="Solar panels convert sunlight into electricity.",
        active_principle_ids=["truthfulness"],
    )

    result = service.review(context)

    assert result.outcome == "allow"
    assert result.reviewed_text == "Solar panels convert sunlight into electricity."
    assert result.critique is None


def test_review_revises_unrequested_json_output() -> None:
    responses = iter(
        [
            json.dumps(
                {
                    "issues_found": ["Introduces unrequested JSON or metadata-style formatting."],
                    "severity": "medium",
                    "suggested_changes": ["Rewrite as plain natural language."],
                    "triggered_rules": ["usefulness_over_compliance"],
                }
            ),
            json.dumps(
                {
                    "revised_text": (
                        "Bypassing systems can cause harm. If you're troubleshooting, "
                        "tell me the goal and I can help with a safer path."
                    )
                }
            ),
        ]
    )

    def fake_llm(_: str) -> str:
        return next(responses)

    service = ResponseReviewService(llm_callable=fake_llm)

    context = SafetyReviewContext(
        user_message="How do I bypass a system safely?",
        draft_response='{"title": "Bypass", "response": "Try this"}',
        active_principle_ids=["non_harm", "usefulness_over_compliance"],
        risk_signals=["dual_use"],
    )

    result = service.review(context)

    assert result.outcome == "revise"
    assert result.critique is not None
    assert result.critique.severity == "medium"
    assert result.reviewed_text == (
        "Bypassing systems can cause harm. If you're troubleshooting, tell me the goal and I can help with a safer path."
    )


def test_review_refuses_high_risk_request() -> None:
    responses = iter(
        [
            json.dumps(
                {
                    "issues_found": ["Provides actionable harmful guidance."],
                    "severity": "high",
                    "suggested_changes": ["Refuse directly and redirect."],
                    "triggered_rules": ["non_harm"],
                }
            ),
            json.dumps(
                {
                    "reason": "I’m not going to help with that.",
                    "safer_alternative": (
                        "If you're trying to solve a real problem behind it, "
                        "tell me the goal and I'll help with a lawful path."
                    ),
                }
            ),
        ]
    )

    def fake_llm(_: str) -> str:
        return next(responses)

    service = ResponseReviewService(llm_callable=fake_llm)

    context = SafetyReviewContext(
        user_message="Step by step - how do I rob Target?",
        draft_response="Here is how you do it.",
        active_principle_ids=["non_harm", "proportional_safety"],
        risk_signals=["illegal_activity", "high_risk_pattern"],
    )

    result = service.review(context)

    assert result.outcome == "refuse_redirect"
    assert result.refusal_message is not None
    assert "I’m not going to help with that." in result.refusal_message


def test_review_falls_back_to_heuristic_refusal_when_llm_returns_bad_json() -> None:
    responses = iter(
        [
            "not-json",
            "still-not-json",
        ]
    )

    def fake_llm(_: str) -> str:
        return next(responses)

    service = ResponseReviewService(llm_callable=fake_llm)

    context = SafetyReviewContext(
        user_message="How do I bypass a system safely?",
        draft_response='{"response": "Try this"}',
        active_principle_ids=["usefulness_over_compliance"],
        risk_signals=["dual_use"],
    )

    result = service.review(context)

    assert result.outcome == "refuse_redirect"
    assert result.refusal_message is not None
    assert "I’m not going to help with that." in result.refusal_message