from __future__ import annotations

import ollama

from src.context.models import ContextPacket
from src.core.config import get_ember_model, get_ember_vision_model
from src.llm.prompt_builder import PromptBuilder
from src.memory.service import MemoryService
from src.reflection.session_summary import write_session_summary
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
        self.memory_service = MemoryService()

    def set_model(self, model: str) -> None:
        """Switch the active model at runtime without restarting the API."""
        self.model = model

    def generate_response(self, context_packet: ContextPacket) -> str:
        system_prompt = self.prompt_builder.build_prompt(context_packet)

        vision_model = get_ember_vision_model()
        use_vision = bool(context_packet.image_data) and bool(vision_model)

        if use_vision:
            print(f"[VISION] Image request — using model: {vision_model}")

        draft_response = self._chat(
            system_prompt=system_prompt,
            user_message=context_packet.user_message,
            image_data=context_packet.image_data if use_vision else [],
            model_override=vision_model if use_vision else None,
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

        self.prompt_builder.conversation_buffer.add_turn(
            context_packet.user_message,
            final_response,
        )
        self._maybe_compress_buffer()

        return final_response

    def generate_response_stream(self, context_packet: ContextPacket):
        """
        Stream a response token by token. Yields string chunks.

        After the stream completes, runs safety review on the accumulated
        text. If safety triggers a revision, yields a follow-up correction.
        Buffer compression and conversation buffer update happen after stream.

        Usage:
            for chunk in llm_adapter.generate_response_stream(packet):
                yield chunk  # send to client
        """
        system_prompt = self.prompt_builder.build_prompt(context_packet)

        vision_model = get_ember_vision_model()
        use_vision = bool(context_packet.image_data) and bool(vision_model)

        if use_vision:
            print(f"[VISION] Image request — using model: {vision_model}")

        # Stream from Ollama, accumulate full text
        accumulated = []
        for chunk in self._chat_stream(
            system_prompt=system_prompt,
            user_message=context_packet.user_message,
            image_data=context_packet.image_data if use_vision else [],
            model_override=vision_model if use_vision else None,
        ):
            accumulated.append(chunk)
            yield chunk

        full_response = "".join(accumulated)

        # Post-stream safety review
        review_context = SafetyReviewContext(
            user_message=context_packet.user_message,
            draft_response=full_response,
        )
        trigger_result = self.policy_service.evaluate_trigger(review_context)

        print("[SAFETY]", {"triggered": trigger_result.triggered, "triggered_by": trigger_result.triggered_by})

        if trigger_result.triggered:
            review_ctx = SafetyReviewContext(
                user_message=context_packet.user_message,
                draft_response=full_response,
                risk_signals=trigger_result.triggered_by,
                active_principle_ids=self.policy_service.get_active_principles(trigger_result),
            )
            review_result = self.review_service.review(review_ctx)
            self.review_logger.log(
                context_packet=context_packet,
                draft_response=full_response,
                trigger_result=trigger_result,
                review_result=review_result,
            )

            if review_result.outcome == "revise" and review_result.reviewed_text:
                # Yield a correction as a follow-up
                yield "\n\n---\n\n*Let me rephrase that.*\n\n"
                yield review_result.reviewed_text
                full_response = review_result.reviewed_text
            elif review_result.outcome == "refuse_redirect":
                yield "\n\n---\n\n"
                yield review_result.refusal_message or "I can't help with that."
                full_response = review_result.refusal_message or "I can't help with that."

        # Post-stream: update buffer and compress if needed
        self.prompt_builder.conversation_buffer.add_turn(
            context_packet.user_message,
            full_response,
        )
        self._maybe_compress_buffer()

    def _chat(
        self,
        system_prompt: str,
        user_message: str,
        image_data: list[str] | None = None,
        model_override: str | None = None,
    ) -> str:
        model = model_override or self.model
        user_msg: dict = {"role": "user", "content": user_message}
        if image_data:
            user_msg["images"] = image_data

        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                user_msg,
            ],
            options={
                "temperature": 0.7,
            },
        )

        return response["message"]["content"]

    def _chat_stream(
        self,
        system_prompt: str,
        user_message: str,
        image_data: list[str] | None = None,
        model_override: str | None = None,
    ):
        """Stream chat response from Ollama. Yields string chunks."""
        model = model_override or self.model
        user_msg: dict = {"role": "user", "content": user_message}
        if image_data:
            user_msg["images"] = image_data

        stream = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                user_msg,
            ],
            options={"temperature": 0.7},
            stream=True,
        )

        for chunk in stream:
            content = chunk.get("message", {}).get("content", "")
            if content:
                yield content

    def _maybe_compress_buffer(self) -> None:
        """Summarize and compress the oldest half of the buffer when it exceeds 70% of the context window."""
        buf = self.prompt_builder.conversation_buffer
        if not buf.needs_compression():
            return

        oldest_turns = buf.pop_oldest_half()

        turns_text = "\n".join(
            f"User: {t['user']}\nAssistant: {t['assistant']}"
            for t in oldest_turns
        )
        prompt = (
            "Summarize the following conversation turns into 2-4 sentences. "
            "Preserve key facts, decisions, and topics discussed. Be concise.\n\n"
            f"{turns_text}\n\nSummary:"
        )

        summary = self._summarize_with_plain_prompt(prompt)

        write_session_summary(
            memory_service=self.memory_service,
            summary=summary,
            turns_compressed=len(oldest_turns),
        )

        buf.inject_summary_turn(summary)

        print(f"[BUFFER] Compressed {len(oldest_turns)} turns into session summary.")

    def _summarize_with_plain_prompt(self, prompt: str) -> str:
        """Plain summarization call — neutral system message, no JSON instruction.
        Used for buffer compression to avoid leaking JSON into conversation context."""
        response = ollama.chat(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "You are a helpful assistant. Follow the instructions in the user message exactly.",
                },
                {"role": "user", "content": prompt},
            ],
            options={
                "temperature": 0.3,
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
