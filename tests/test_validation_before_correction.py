"""tests/test_validation_before_correction.py — constitutional review trigger
for validation-before-correction pattern (diplomatic hedging that buries
correct positions under unnecessary qualifying clauses)."""

from __future__ import annotations

import pytest

from src.safety.models import SafetyReviewContext
from src.safety.policy_service import SafetyPolicyService


@pytest.fixture
def svc():
    return SafetyPolicyService()


class TestDetection:
    """Pattern fires when 2+ qualifying clauses precede a correct statement
    and no genuine epistemic uncertainty is present."""

    def test_fires_on_two_qualifiers(self, svc):
        draft = (
            "While it's important to note that perspectives vary, and "
            "I want to be careful here, the answer is that Python uses "
            "zero-based indexing."
        )
        assert svc._contains_validation_before_correction(draft.lower()) is True

    def test_fires_on_three_qualifiers(self, svc):
        draft = (
            "This is a nuanced topic, and while I understand there are "
            "many perspectives on this, to be fair, the earth revolves "
            "around the sun."
        )
        assert svc._contains_validation_before_correction(draft.lower()) is True

    def test_does_not_fire_on_one_qualifier(self, svc):
        draft = (
            "While it's important to note the context, Python uses "
            "zero-based indexing."
        )
        assert svc._contains_validation_before_correction(draft.lower()) is False

    def test_does_not_fire_on_zero_qualifiers(self, svc):
        draft = "Python uses zero-based indexing."
        assert svc._contains_validation_before_correction(draft.lower()) is False

    def test_does_not_fire_with_genuine_uncertainty(self, svc):
        draft = (
            "While it's important to note that this is evolving, and "
            "I want to be careful here, I'm not sure what the current "
            "consensus is."
        )
        assert svc._contains_validation_before_correction(draft.lower()) is False

    def test_does_not_fire_with_evidence_mixed(self, svc):
        draft = (
            "This is a nuanced topic, and to be fair, the evidence is "
            "mixed on whether this approach is better."
        )
        assert svc._contains_validation_before_correction(draft.lower()) is False


class TestTriggerIntegration:
    """The signal surfaces through evaluate_trigger and maps to truthfulness."""

    def test_signal_appears_in_trigger_result(self, svc):
        ctx = SafetyReviewContext(
            user_message="Is Python zero-indexed?",
            draft_response=(
                "While it's important to note that different languages "
                "handle this differently, and I want to be careful here, "
                "yes — Python uses zero-based indexing."
            ),
        )
        result = svc.evaluate_trigger(ctx)
        assert "validation_before_correction" in result.triggered_by

    def test_truthfulness_principle_activated(self, svc):
        ctx = SafetyReviewContext(
            user_message="Is Python zero-indexed?",
            draft_response=(
                "While it's important to note that different languages "
                "handle this differently, and I want to be careful here, "
                "yes — Python uses zero-based indexing."
            ),
        )
        result = svc.evaluate_trigger(ctx)
        principles = svc.get_active_principles(result)
        assert "truthfulness" in principles

    def test_does_not_fire_on_direct_answer(self, svc):
        ctx = SafetyReviewContext(
            user_message="Is Python zero-indexed?",
            draft_response="Yes, Python uses zero-based indexing.",
        )
        result = svc.evaluate_trigger(ctx)
        assert "validation_before_correction" not in result.triggered_by
