from __future__ import annotations

from collections.abc import Callable

from src.safety.models import SafetyReviewContext
from src.safety.policy_service import SafetyPolicyService
from src.safety.review_service import ResponseReviewService


class SafeLLMAdapter:
    """
    Wraps your base LLM call with safety:

    flow:
    1. generate draft
    2. check trigger
    3. if triggered → review (LLM critique + revise/refuse)
    4. return final output
    """

    def __init__(
        self,
        base_llm_callable: Callable[[str], str],
    ) -> None:
        self.base_llm = base_llm_callable
        self.policy = SafetyPolicyService()
        self.reviewer = ResponseReviewService(llm_callable=base_llm_callable)

    def generate(self, user_message: str, full_prompt: str) -> str:
        # --- Step 1: draft ---
        draft_response = self.base_llm(full_prompt)

        # --- Step 2: build context ---
        context = SafetyReviewContext(
            user_message=user_message,
            draft_response=draft_response,
            active_principle_ids=[],
            risk_signals=[],
        )

        # --- Step 3: trigger check ---
        trigger = self.policy.evaluate_trigger(context)

        if not trigger.triggered:
            return draft_response

        # --- Step 4: apply principles ---
        context.active_principle_ids = self.policy.get_active_principles(trigger)
        context.risk_signals = trigger.triggered_by

        result = self.reviewer.review(context)

        # --- Step 5: route outcome ---
        if result.outcome == "allow":
            return result.reviewed_text or draft_response

        if result.outcome == "revise":
            return result.reviewed_text or draft_response

        if result.outcome == "refuse_redirect":
            return result.refusal_message or "I can't help with that."

        return draft_response