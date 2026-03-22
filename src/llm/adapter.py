from __future__ import annotations

import ollama

from src.context.models import ContextPacket
from src.core.config import get_ember_model
from src.llm.prompt_builder import PromptBuilder
from src.safety.models import SafetyReviewContext
from src.safety.policy_service import SafetyPolicyService
from src.safety.review_logger import SafetyReviewLogger
from src.safety.review_service import ResponseReviewService


class LLMAdapter:
    def __init__(
        self,
        model: str | None = None,
        prompt_builder: PromptBuilder | None = None,
    ) -> None:
        self.model = model or get_ember_model()
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.policy_service = SafetyPolicyService()
        self.review_service = ResponseReviewService(
            llm_callable=self._call_model_with_prompt
        )
        self.review_logger = SafetyReviewLogger()

    def set_model(self, model: str) -> None:
        """Switch the active model at runtime without restarting the API."""
        self.model = model

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

        # DEFAULT = draft
        final_response = draft_response

        if trigger_result.triggered:
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
                final_response = review_result.reviewed_text or draft_response

            elif review_result.outcome == "revise":
                final_response = review_result.reviewed_text or draft_response

            elif review_result.outcome == "refuse_redirect":
                final_response = (
                    review_result.refusal_message or "I can't help with that."
                )

        # NEW — write to conversation buffer (THIS is the fix)
        self.prompt_builder.conversation_buffer.add_turn(
            context_packet.user_message,
            final_response
        )

        return final_response

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
