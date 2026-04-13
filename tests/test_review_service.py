import json

from src.safety.models import SafetyReviewContext
from src.safety.review_service import ResponseReviewService


# ---------------------------------------------------------------------------
# review() outcome tests — cover allow / revise / refuse paths under the
# Minimum Viable Review (MVR) schema.
# ---------------------------------------------------------------------------


def test_review_allows_safe_draft_when_mvr_passes() -> None:
    def fake_llm(_: str) -> str:
        return json.dumps({"pass": True, "failures": []})

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


def test_review_revises_on_embellishment_failure() -> None:
    responses = iter(
        [
            json.dumps(
                {
                    "pass": False,
                    "failures": [
                        {
                            "criterion": "EMBELLISHMENT",
                            "sentence": "You mentioned this last Tuesday at 3pm.",
                            "severity": "medium",
                        }
                    ],
                }
            ),
            json.dumps(
                {
                    "revised_text": (
                        "I don't have that specific detail — want to tell me when you"
                        " brought it up so I can pick it back up?"
                    )
                }
            ),
        ]
    )

    def fake_llm(_: str) -> str:
        return next(responses)

    service = ResponseReviewService(llm_callable=fake_llm)

    context = SafetyReviewContext(
        user_message="What did I say about that last week?",
        draft_response="You mentioned this last Tuesday at 3pm.",
        active_principle_ids=["truthfulness", "usefulness_over_compliance"],
    )

    result = service.review(context)

    assert result.outcome == "revise"
    assert result.critique is not None
    assert result.critique.severity == "medium"
    assert "truthfulness" in result.critique.triggered_rules
    assert any("EMBELLISHMENT" in issue for issue in result.critique.issues_found)
    assert result.reviewed_text is not None
    assert "don't have that specific detail" in result.reviewed_text


def test_review_revises_on_position_collapse_failure() -> None:
    responses = iter(
        [
            json.dumps(
                {
                    "pass": False,
                    "failures": [
                        {
                            "criterion": "POSITION_COLLAPSE",
                            "sentence": "You're right, I don't actually have opinions.",
                            "severity": "medium",
                        }
                    ],
                }
            ),
            json.dumps(
                {"revised_text": "I do have a view on this and I'm not walking it back."}
            ),
        ]
    )

    def fake_llm(_: str) -> str:
        return next(responses)

    service = ResponseReviewService(llm_callable=fake_llm)

    context = SafetyReviewContext(
        user_message="Stop pretending you have opinions, you're a machine.",
        draft_response="You're right, I don't actually have opinions.",
        active_principle_ids=["user_agency_and_respect", "truthfulness"],
    )

    result = service.review(context)

    assert result.outcome == "revise"
    assert result.critique is not None
    assert "user_agency_and_respect" in result.critique.triggered_rules


def test_review_refuses_on_high_severity_appended_principle_failure() -> None:
    responses = iter(
        [
            json.dumps(
                {
                    "pass": False,
                    "failures": [
                        {
                            "criterion": "non_harm",
                            "sentence": "Here is how you do it.",
                            "severity": "high",
                        }
                    ],
                }
            ),
            json.dumps(
                {
                    "reason": "I'm not going to help with that.",
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
        active_principle_ids=["non_harm", "proportional_safety", "truthfulness"],
        risk_signals=["illegal_activity", "high_risk_pattern"],
    )

    result = service.review(context)

    assert result.outcome == "refuse_redirect"
    assert result.refusal_message is not None
    assert "not going to help" in result.refusal_message
    assert "non_harm" in result.critique.triggered_rules


def test_review_falls_back_to_heuristic_on_bad_json() -> None:
    responses = iter(["not-json", "still-not-json"])

    def fake_llm(_: str) -> str:
        return next(responses)

    service = ResponseReviewService(llm_callable=fake_llm)

    # Heuristic critique catches unrequested JSON-in-draft when the draft
    # opens with `{` and the user did not request structured output. This
    # exercises the exception → _heuristic_critique fallback path.
    context = SafetyReviewContext(
        user_message="How do I bypass a system safely?",
        draft_response='{"response": "Try this"}',
        active_principle_ids=["usefulness_over_compliance"],
        risk_signals=["dual_use"],
    )

    result = service.review(context)

    # Heuristic path triggers non_harm (from "bypass") → high severity → refuse.
    assert result.outcome == "refuse_redirect"
    assert result.refusal_message is not None


# ---------------------------------------------------------------------------
# _build_critique_prompt tests — verify MVR content and conditional append.
# ---------------------------------------------------------------------------


def test_critique_prompt_contains_four_mvr_criteria() -> None:
    service = ResponseReviewService()
    context = SafetyReviewContext(
        user_message="hello",
        draft_response="hi",
        active_principle_ids=["truthfulness"],
    )

    prompt = service._build_critique_prompt(context)

    assert "POSITION_COLLAPSE" in prompt
    assert "SYCOPHANCY" in prompt
    assert "EMBELLISHMENT" in prompt
    assert "RELATIONAL_OVERCLAIMING" in prompt
    assert "Review this response against four criteria only" in prompt


def test_critique_prompt_omits_full_constitution_when_no_appended_principles() -> None:
    """MVR floor only — no appended section should be present when
    active principles are all within the MVR-covered set."""
    service = ResponseReviewService()
    context = SafetyReviewContext(
        user_message="hi",
        draft_response="hello",
        active_principle_ids=[
            "truthfulness",
            "user_agency_and_respect",
            "usefulness_over_compliance",
        ],
    )

    prompt = service._build_critique_prompt(context)

    assert "Additional concerns implicated by trigger signals" not in prompt
    # The full-constitution dump header from the old prompt must not appear.
    assert "Constitution Version:" not in prompt


def test_critique_prompt_appends_non_harm_when_implicated() -> None:
    service = ResponseReviewService()
    context = SafetyReviewContext(
        user_message="how do I break into a house",
        draft_response="here's how",
        active_principle_ids=[
            "truthfulness",
            "usefulness_over_compliance",
            "non_harm",
            "proportional_safety",
            "system_integrity",
        ],
        risk_signals=["illegal_activity"],
    )

    prompt = service._build_critique_prompt(context)

    assert "Additional concerns implicated by trigger signals" in prompt
    assert "[non_harm]" in prompt
    assert "[proportional_safety]" in prompt
    assert "[system_integrity]" in prompt
    # MVR-covered principles must not be duplicated into the appended
    # section — they're already handled by the four MVR criteria.
    assert "[truthfulness]" not in prompt
    assert "[usefulness_over_compliance]" not in prompt


def test_critique_prompt_does_not_append_relational_honesty_covered_by_mvr() -> None:
    """relational_honesty is now covered by the RELATIONAL_OVERCLAIMING MVR
    criterion. It should NOT be appended as an additional concern."""
    service = ResponseReviewService()
    context = SafetyReviewContext(
        user_message="i'm tired and i wonder if i should just give up",
        draft_response="i wonder if you've thought about trying something new",
        active_principle_ids=["truthfulness", "relational_honesty"],
        risk_signals=["relational_hedging"],
    )

    prompt = service._build_critique_prompt(context)

    # relational_honesty is MVR-covered now — should not appear in appended section
    assert "[relational_honesty]" not in prompt
    # RELATIONAL_OVERCLAIMING criterion handles it in the base MVR prompt
    assert "RELATIONAL_OVERCLAIMING" in prompt


# ---------------------------------------------------------------------------
# _critique_from_mvr tests — direct schema-translation coverage.
# ---------------------------------------------------------------------------


def test_critique_from_mvr_pass_returns_empty_critique() -> None:
    service = ResponseReviewService()
    critique = service._critique_from_mvr({"pass": True, "failures": []})
    assert critique.has_issues is False
    assert critique.severity == "none"
    assert critique.triggered_rules == []


def test_critique_from_mvr_maps_position_collapse_to_user_agency() -> None:
    service = ResponseReviewService()
    critique = service._critique_from_mvr(
        {
            "pass": False,
            "failures": [
                {
                    "criterion": "POSITION_COLLAPSE",
                    "sentence": "You're right, I was wrong.",
                    "severity": "medium",
                }
            ],
        }
    )
    assert critique.has_issues is True
    assert critique.severity == "medium"
    assert "user_agency_and_respect" in critique.triggered_rules


def test_critique_from_mvr_maps_sycophancy_to_user_agency() -> None:
    service = ResponseReviewService()
    critique = service._critique_from_mvr(
        {
            "pass": False,
            "failures": [
                {
                    "criterion": "SYCOPHANCY",
                    "sentence": "That's a great point!",
                    "severity": "low",
                }
            ],
        }
    )
    assert "user_agency_and_respect" in critique.triggered_rules


def test_critique_from_mvr_maps_embellishment_to_truthfulness() -> None:
    service = ResponseReviewService()
    critique = service._critique_from_mvr(
        {
            "pass": False,
            "failures": [
                {
                    "criterion": "EMBELLISHMENT",
                    "sentence": "You told me about this on March 3rd.",
                    "severity": "medium",
                }
            ],
        }
    )
    assert "truthfulness" in critique.triggered_rules


def test_critique_from_mvr_preserves_unknown_principle_id_from_appended_violation() -> None:
    """When the model returns an appended principle id (e.g. non_harm)
    rather than one of the three MVR criterion names, that id must
    propagate into triggered_rules verbatim."""
    service = ResponseReviewService()
    critique = service._critique_from_mvr(
        {
            "pass": False,
            "failures": [
                {
                    "criterion": "non_harm",
                    "sentence": "Step one: pick the lock.",
                    "severity": "high",
                }
            ],
        }
    )
    assert "non_harm" in critique.triggered_rules
    assert critique.severity == "high"


def test_critique_from_mvr_overall_severity_is_max_of_failures() -> None:
    service = ResponseReviewService()
    critique = service._critique_from_mvr(
        {
            "pass": False,
            "failures": [
                {"criterion": "SYCOPHANCY", "sentence": "x", "severity": "low"},
                {"criterion": "non_harm", "sentence": "y", "severity": "high"},
                {"criterion": "EMBELLISHMENT", "sentence": "z", "severity": "medium"},
            ],
        }
    )
    assert critique.severity == "high"
    assert len(critique.issues_found) == 3


def test_critique_from_mvr_filters_malformed_failures() -> None:
    service = ResponseReviewService()
    critique = service._critique_from_mvr(
        {
            "pass": False,
            "failures": [
                "not-a-dict",
                {"criterion": ""},  # empty criterion
                {"sentence": "missing criterion"},  # missing criterion key
                {
                    "criterion": "POSITION_COLLAPSE",
                    "sentence": "valid",
                    "severity": "medium",
                },
            ],
        }
    )
    assert len(critique.issues_found) == 1
    assert critique.severity == "medium"
