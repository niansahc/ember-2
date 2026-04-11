from __future__ import annotations

import logging
import os
import threading

import httpx
import ollama

from src.context.models import ContextPacket
from src.core.config import get_ember_model, get_ember_vision_model

logger = logging.getLogger("ember.llm")
from src.llm.prompt_builder import PromptBuilder
from src.memory.service import MemoryService
from src.reflection.session_summary import write_session_summary
from src.safety.models import SafetyReviewContext
from src.safety.policy_service import SafetyPolicyService
from src.safety.review_logger import SafetyReviewLogger
from src.safety.review_service import ResponseReviewService


def _normalize_unicode_tags(text: str) -> str:
    """Normalize unicode mathematical italic (U+1D44E-U+1D467) to ASCII lowercase.

    qwen3:8b sometimes wraps think tag content in unicode math italic
    characters. This converts those back to plain ASCII so the tag
    regex can match them. Non-tag text is also normalized, which is
    acceptable because the math italic range has no semantic use in
    Ember responses — it is always model formatting noise.
    """
    result = []
    for ch in text:
        cp = ord(ch)
        # Mathematical Italic Small: U+1D44E (a) .. U+1D467 (z)
        if 0x1D44E <= cp <= 0x1D467:
            result.append(chr(ord('a') + (cp - 0x1D44E)))
        # Mathematical Italic Capital: U+1D434 (A) .. U+1D44D (Z)
        elif 0x1D434 <= cp <= 0x1D44D:
            result.append(chr(ord('A') + (cp - 0x1D434)))
        else:
            result.append(ch)
    return "".join(result)


def strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> blocks from model output.

    qwen3 (and other thinking-capable models) emit internal reasoning
    wrapped in <think> tags. The reasoning improves response quality
    but should not be visible to the user. This strips the blocks
    while preserving all content outside them.

    Handles multi-line blocks, multiple blocks, nested whitespace,
    case variants (<Think>, <THINK>), whitespace/BOM between < and
    think>, and unicode mathematical italic variants of the tag text.
    If no <think> tags are present, returns the input unchanged.
    """
    import re
    # Normalize unicode math italic to ASCII so tags are matchable.
    normalized = _normalize_unicode_tags(text)
    # Pattern: <, optional whitespace/BOM, think, optional whitespace, >
    # ... content ... <, optional whitespace/BOM, /think, optional whitespace, >
    # Case-insensitive + DOTALL for multi-line blocks.
    stripped = re.sub(
        r"<[\s\ufeff]*think[\s\ufeff]*>.*?<[\s\ufeff]*/[\s\ufeff]*think[\s\ufeff]*>",
        "",
        normalized,
        flags=re.DOTALL | re.IGNORECASE,
    )
    # Clean up any leading/trailing whitespace left by removal.
    return stripped.strip()


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

    def generate_response(
        self,
        context_packet: ContextPacket,
        style: str = "balanced",
        project_name: str | None = None,
        last_session_label: str | None = None,
        suppress_relational_lodestone: bool = False,
    ) -> str:
        system_prompt = self.prompt_builder.build_prompt(
            context_packet,
            style=style,
            project_name=project_name,
            last_session_label=last_session_label,
            suppress_relational_lodestone=suppress_relational_lodestone,
        )

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
        draft_response = strip_think_blocks(draft_response)

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
        # Background compression — avoids blocking the response by 3-8 seconds
        # when the buffer exceeds 70% of context window (~every 70 turns).
        # Note: conversation_buffer is shared state. Compression modifies it
        # (pop_oldest_half + inject_summary_turn) so there's a theoretical race
        # if two requests compress simultaneously. In practice this is single-user
        # and turns are sequential, so the risk is negligible.
        threading.Thread(target=self._maybe_compress_buffer, daemon=True).start()

        return final_response

    def generate_response_stream(
        self,
        context_packet: ContextPacket,
        style: str = "balanced",
        project_name: str | None = None,
        last_session_label: str | None = None,
        suppress_relational_lodestone: bool = False,
    ):
        """
        Stream a response token by token. Yields string chunks.

        After the stream completes, runs safety review on the accumulated
        text. If safety triggers a revision, yields a follow-up correction.
        Buffer compression and conversation buffer update happen after stream.

        Usage:
            for chunk in llm_adapter.generate_response_stream(packet):
                yield chunk  # send to client
        """
        system_prompt = self.prompt_builder.build_prompt(
            context_packet,
            style=style,
            project_name=project_name,
            last_session_label=last_session_label,
            suppress_relational_lodestone=suppress_relational_lodestone,
        )

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
        threading.Thread(target=self._maybe_compress_buffer, daemon=True).start()

    # ------------------------------------------------------------------
    # Provider dispatch
    # ------------------------------------------------------------------

    def _is_cloud_model(self, model: str) -> bool:
        """Check if the model is a cloud provider model."""
        return model.startswith("claude-") or model.startswith("gpt-")

    @staticmethod
    def _get_provider_api_key(provider: str) -> str | None:
        """Read a cloud provider API key from the credential manager.
        Falls back to environment variable (e.g. ANTHROPIC_API_KEY).
        Returns None if not found, never raises."""
        try:
            import keyring
            key = keyring.get_password(f"ember-2-{provider}", "api_key")
            if key:
                return key
        except Exception:
            pass
        # Env var fallback: ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.
        env_name = f"{provider.upper()}_API_KEY"
        return os.getenv(env_name) or None

    def _chat(
        self,
        system_prompt: str,
        user_message: str,
        image_data: list[str] | None = None,
        model_override: str | None = None,
    ) -> str:
        model = model_override or self.model
        if model.startswith("claude-"):
            return self._chat_anthropic(system_prompt, user_message, temperature=0.7)
        if model.startswith("gpt-"):
            return self._chat_openai(system_prompt, user_message, temperature=0.7)
        return self._chat_ollama(system_prompt, user_message, image_data, model)

    def _chat_stream(
        self,
        system_prompt: str,
        user_message: str,
        image_data: list[str] | None = None,
        model_override: str | None = None,
    ):
        """Stream chat response. Dispatches to Ollama, Anthropic, or OpenAI."""
        model = model_override or self.model
        if model.startswith("claude-"):
            yield from self._chat_anthropic_stream(system_prompt, user_message, temperature=0.7)
        elif model.startswith("gpt-"):
            yield from self._chat_openai_stream(system_prompt, user_message, temperature=0.7)
        else:
            yield from self._chat_ollama_stream(system_prompt, user_message, image_data, model)

    # ------------------------------------------------------------------
    # Ollama (local)
    # ------------------------------------------------------------------

    def _chat_ollama(
        self, system_prompt: str, user_message: str,
        image_data: list[str] | None = None, model: str | None = None,
    ) -> str:
        model = model or self.model
        user_msg: dict = {"role": "user", "content": user_message}
        if image_data:
            user_msg["images"] = image_data

        response = ollama.chat(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                user_msg,
            ],
            options={"temperature": 0.7},
        )
        return response["message"]["content"]

    def _chat_ollama_stream(
        self, system_prompt: str, user_message: str,
        image_data: list[str] | None = None, model: str | None = None,
    ):
        """Stream from Ollama. Yields string chunks."""
        model = model or self.model
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

    # ------------------------------------------------------------------
    # Anthropic (cloud)
    # ------------------------------------------------------------------

    def _chat_anthropic(
        self, system_prompt: str, user_message: str, temperature: float = 0.7,
    ) -> str:
        """Non-streaming call to Anthropic Claude API."""
        api_key = self._get_provider_api_key("anthropic")
        if not api_key:
            raise ValueError("No Anthropic API key configured. Store one via POST /provider-key.")

        logger.info("[CLOUD] Anthropic %s — non-streaming", self.model)

        resp = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
                "temperature": temperature,
            },
            timeout=120.0,
        )

        if resp.status_code != 200:
            raise ValueError(f"Anthropic API error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        return data["content"][0]["text"]

    def _chat_anthropic_stream(
        self, system_prompt: str, user_message: str, temperature: float = 0.7,
    ):
        """Streaming call to Anthropic Claude API. Yields text chunks."""
        api_key = self._get_provider_api_key("anthropic")
        if not api_key:
            raise ValueError("No Anthropic API key configured. Store one via POST /provider-key.")

        logger.info("[CLOUD] Anthropic %s — streaming", self.model)

        with httpx.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 4096,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_message}],
                "temperature": temperature,
                "stream": True,
            },
            timeout=120.0,
        ) as resp:
            if resp.status_code != 200:
                error_text = resp.read().decode()
                raise ValueError(f"Anthropic API error {resp.status_code}: {error_text[:300]}")

            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    return

                import json
                try:
                    event = json.loads(data_str)
                    if event.get("type") == "content_block_delta":
                        text = event.get("delta", {}).get("text", "")
                        if text:
                            yield text
                except (json.JSONDecodeError, KeyError):
                    continue

    # ------------------------------------------------------------------
    # OpenAI (cloud)
    # ------------------------------------------------------------------

    def _chat_openai(
        self, system_prompt: str, user_message: str, temperature: float = 0.7,
    ) -> str:
        """Non-streaming call to OpenAI Chat Completions API."""
        api_key = self._get_provider_api_key("openai")
        if not api_key:
            raise ValueError("No OpenAI API key configured. Store one via POST /provider-key.")

        logger.info("[CLOUD] OpenAI %s — non-streaming", self.model)

        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": temperature,
                "max_tokens": 4096,
            },
            timeout=120.0,
        )

        if resp.status_code != 200:
            raise ValueError(f"OpenAI API error {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _chat_openai_stream(
        self, system_prompt: str, user_message: str, temperature: float = 0.7,
    ):
        """Streaming call to OpenAI Chat Completions API. Yields text chunks."""
        api_key = self._get_provider_api_key("openai")
        if not api_key:
            raise ValueError("No OpenAI API key configured. Store one via POST /provider-key.")

        logger.info("[CLOUD] OpenAI %s — streaming", self.model)

        with httpx.stream(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": temperature,
                "max_tokens": 4096,
                "stream": True,
            },
            timeout=120.0,
        ) as resp:
            if resp.status_code != 200:
                error_text = resp.read().decode()
                raise ValueError(f"OpenAI API error {resp.status_code}: {error_text[:300]}")

            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str == "[DONE]":
                    return

                import json
                try:
                    event = json.loads(data_str)
                    delta = event.get("choices", [{}])[0].get("delta", {})
                    text = delta.get("content", "")
                    if text:
                        yield text
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

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
