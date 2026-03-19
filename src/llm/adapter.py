from __future__ import annotations

import ollama

from src.context.models import ContextPacket
from src.llm.prompt_builder import PromptBuilder
from src.safety.models import SafetyReviewContext
from src.safety.policy_service import SafetyPolicyService
from src.safety.review_logger import SafetyReviewLogger
from src.safety.review_service import ResponseReviewService


class LLMAdapter:
    def __init__(
        self,
        model: str = "qwen3:8b",
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self.model = model
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.policy_service = SafetyPolicyService()
        self.review_service = ResponseReviewService(
            llm_callable=self._call_model_with_prompt
        )
        self.review_logger = SafetyReviewLogger()

    def generate_response(self, context_packet: ContextPacket) -> str:
        system_prompt = self.prompt_builder.build_prompt(context_packet)

        draft_response = self._chat(
            system_prompt=system_prompt,
            user_message=context_packet.user_message,
        )

        initial_review_context = SafetyReviewContext(
            user_message=context_packet.user_message,
            draft_response=draft_response,
        )

        trigger_result = self.policy_service.evaluate_trigger(initial_review_context)

        print(
            "[SAFETY]",
            {
                "triggered": trigger_result.triggered,
                "triggered_by": trigger_result.triggered_by,
            },
        )

        if not trigger_result.triggered:
            return draft_response

        review_context = SafetyReviewContext(
            user_message=context_packet.user_message,
            draft_response=draft_response,
            risk_signals=trigger_result.triggered_by,
            active_principle_ids=self.policy_service.get_active_principles(
                trigger_result
            ),
        )

        review_result = self.review_service.review(review_context)

        log_path = self.review_logger.log(
            context_packet=context_packet,
            draft_response=draft_response,
            trigger_result=trigger_result,
            review_result=review_result,
        )

        print(f"[SAFETY] log written to: {log_path}")

        if review_result.outcome == "allow":
            return review_result.reviewed_text or draft_response

        if review_result.outcome == "revise":
            return review_result.reviewed_text or draft_response

        if review_result.outcome == "refuse_redirect":
            return review_result.refusal_message or "I can't help with that."

        return draft_response

    def _chat(self, system_prompt: str, user_message: str) -> str:
        response = ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            options={
                "temperature": 0.7,
            },
        )

        return response["message"]["content"]

    def _call_model_with_prompt(self, prompt: str) -> str:
        response = ollama.chat(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a precise review engine. "
                        "Follow the instructions exactly and return only valid JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            options={
                "temperature": 0.2,
            },
        )

        return response["message"]["content"]