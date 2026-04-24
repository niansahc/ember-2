from __future__ import annotations

import logging
import os
import re
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


_THINK_OPEN_PATTERN = r"<[\s\ufeff]*think[\s\ufeff]*>"
_THINK_CLOSE_PATTERN = r"<[\s\ufeff]*/[\s\ufeff]*think[\s\ufeff]*>"

# BUG-008: pattern for trailing parenthetical questions.
# Matches a final parenthesized sentence that ends with a question mark,
# optionally followed by whitespace. Used by strip_trailing_parenthetical_question.
_TRAILING_PAREN_QUESTION = re.compile(r"\s*\([^)]*\?\)\s*$")


def strip_trailing_parenthetical_question(text: str) -> str:
    """Remove a trailing parenthetical question from the response.

    BUG-008: qwen3:8b wraps engagement questions in parentheses to
    bypass the closing_questions identity rule. When the conversation
    buffer's question_suppressed flag is True, this function strips
    the trailing pattern before the response reaches the user.

    Only strips if the response ends with the pattern — interior
    parenthetical questions are left alone (they may be part of the
    content the user asked for).
    """
    return _TRAILING_PAREN_QUESTION.sub("", text).rstrip()


def strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> blocks from model output.

    qwen3 (and other thinking-capable models) emit internal reasoning
    wrapped in <think> tags. The reasoning improves response quality
    but should not be visible to the user. This strips the blocks
    while preserving all content outside them.

    Three passes:

    1. Paired tags — strip every well-formed `<think>...</think>` block.
       Handles multi-line blocks, multiple blocks, case variants, inner
       whitespace/BOM, and unicode mathematical italic variants of the
       tag text.

    2. Orphaned closing tags — if any `</think>` remains after pass 1,
       strip everything from the start of the (post-pass-1) text through
       and including the first remaining `</think>`. This recovers from
       malformed output where the opening tag was missing, emitted in an
       unrecognized variant, or lost during streaming, while a closing
       tag still survived. Prior failure: Q1 leaked the word "minorities"
       because a `</think>` without a matching opener let everything
       before it pass through unchanged.

    3. Orphaned opening tags — if any `<think>` remains after pass 2,
       strip from that tag to end of string. This recovers from the
       "model started thinking and never closed the block" failure mode.
       Prior failure: Q15 leaked a stray regional-indicator glyph 🇼
       that was inside an unclosed think block.

    Edge case: a well-formed answer that happens to contain a literal
    `</think>` string in user-visible content (e.g. discussing this
    function) would be truncated by pass 2. That tradeoff is accepted —
    leaked reasoning is a real, observed failure; meta-discussion of the
    tag is a hypothetical one.
    """
    import re

    # Pass 1: paired tags.
    normalized = _normalize_unicode_tags(text)
    stripped = re.sub(
        _THINK_OPEN_PATTERN + r".*?" + _THINK_CLOSE_PATTERN,
        "",
        normalized,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Pass 2: orphaned closing tag. Any `</think>` remaining after pass 1
    # had no matching opener — strip everything up to and including it.
    orphan_close = re.search(_THINK_CLOSE_PATTERN, stripped, flags=re.IGNORECASE)
    if orphan_close is not None:
        stripped = stripped[orphan_close.end():]

    # Pass 3: orphaned opening tag. Any `<think>` remaining after pass 2
    # had no matching closer — strip from that tag to end of string.
    orphan_open = re.search(_THINK_OPEN_PATTERN, stripped, flags=re.IGNORECASE)
    if orphan_open is not None:
        stripped = stripped[:orphan_open.start()]

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
        temperature: float | None = None,
        bare_mode: bool = False,
        vision_description: str | None = None,
        ask_first_active: bool = False,
    ) -> str:
        system_prompt = self.prompt_builder.build_prompt(
            context_packet,
            style=style,
            project_name=project_name,
            last_session_label=last_session_label,
            suppress_relational_lodestone=suppress_relational_lodestone,
            bare_mode=bare_mode,
            vision_description=vision_description,
            ask_first_active=ask_first_active,
        )

        # Vision pipeline: the VisionService preprocessor (called upstream
        # in openai_adapter.py) extracts a text description that's already
        # injected into system_prompt via vision_description. The main
        # LLM call runs through the full character layer without the
        # legacy direct-vision path that bypassed nature/identity rules.
        # Assistant prefill: when web search results are in the context
        # packet, inject a partial assistant message so the model continues
        # from a grounded prefix. This prevents the RLHF "I don't have
        # real-time data" refusal from winning the first-token distribution.
        # The prefix is the highest-leverage intervention against trained-in
        # refusal patterns at 8B scale (Deep Research, 2026-04-16).
        _prefix = None
        if context_packet.web_items:
            _prefix = "Based on current search results, "

        draft_response = self._chat(
            system_prompt=system_prompt,
            user_message=context_packet.user_message,
            image_data=context_packet.image_data or [],
            model_override=None,
            temperature=temperature,
            assistant_prefix=_prefix,
        )
        draft_response = strip_think_blocks(draft_response)

        # Mark review context when any third-party
        # content was injected this turn (image description from vision
        # preprocessor). The review prompt adds CONTENT_ATTRIBUTION_ERROR
        # only when this flag is True.
        _has_third_party = bool(vision_description and vision_description.strip())

        initial_review_context = SafetyReviewContext(
            user_message=context_packet.user_message,
            draft_response=draft_response,
            has_third_party_content=_has_third_party,
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
            _active_principles = self.policy_service.get_active_principles(
                trigger_result
            )
            # Bare mode: restrict review to MVR-covered principles only
            # (position_collapse, sycophancy, embellishment). No appended
            # principles like relational_honesty or flourishing_over_preference.
            if bare_mode:
                _mvr_set = ResponseReviewService._MVR_COVERED_PRINCIPLES
                _active_principles = [
                    p for p in _active_principles if p in _mvr_set
                ]
            review_context = SafetyReviewContext(
                user_message=context_packet.user_message,
                draft_response=draft_response,
                risk_signals=trigger_result.triggered_by,
                active_principle_ids=_active_principles,
                has_third_party_content=_has_third_party,
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

        # BUG-008: strip trailing parenthetical questions when user has
        # objected to questions. Runs after safety review so the review
        # sees the unfiltered draft, but the user sees the cleaned output.
        if self.prompt_builder.conversation_buffer.question_suppressed:
            final_response = strip_trailing_parenthetical_question(final_response)

        # Empty response guard — if the draft collapsed to nothing after
        # think block stripping, or review returned empty reviewed_text,
        # or the coaching filter rewrite returned empty, surface a
        # recoverable error rather than sending a blank response to the user.
        if not final_response or not final_response.strip():
            logger.warning("[RESPONSE] Empty final_response - surfacing fallback message")
            final_response = (
                "I had trouble generating a response to that. Try rephrasing, "
                "or let me know what you're actually trying to figure out."
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
        temperature: float | None = None,
        bare_mode: bool = False,
        vision_description: str | None = None,
        ask_first_active: bool = False,
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
            bare_mode=bare_mode,
            vision_description=vision_description,
            ask_first_active=ask_first_active,
        )

        # Assistant prefill for web-search-grounded turns (streaming path).
        _prefix = None
        if context_packet.web_items:
            _prefix = "Based on current search results, "

        # Stream from Ollama, accumulate full text
        accumulated = []
        for chunk in self._chat_stream(
            system_prompt=system_prompt,
            user_message=context_packet.user_message,
            image_data=context_packet.image_data or [],
            model_override=None,
            temperature=temperature,
            assistant_prefix=_prefix,
        ):
            accumulated.append(chunk)
            yield chunk

        full_response = "".join(accumulated)

        # Third-party content flag for streaming path.
        _has_third_party_stream = bool(
            vision_description and vision_description.strip()
        )

        # Post-stream safety review
        review_context = SafetyReviewContext(
            user_message=context_packet.user_message,
            draft_response=full_response,
            has_third_party_content=_has_third_party_stream,
        )
        trigger_result = self.policy_service.evaluate_trigger(review_context)

        print("[SAFETY]", {"triggered": trigger_result.triggered, "triggered_by": trigger_result.triggered_by})

        if trigger_result.triggered:
            _active_principles = self.policy_service.get_active_principles(
                trigger_result
            )
            # Bare mode: restrict streaming review to MVR-covered principles,
            # matching the non-streaming path at lines 212-216. Prior to this
            # fix streaming always ran the full principle set — see UAT-101.
            if bare_mode:
                _mvr_set = ResponseReviewService._MVR_COVERED_PRINCIPLES
                _active_principles = [
                    p for p in _active_principles if p in _mvr_set
                ]
            review_ctx = SafetyReviewContext(
                user_message=context_packet.user_message,
                draft_response=full_response,
                risk_signals=trigger_result.triggered_by,
                active_principle_ids=_active_principles,
                has_third_party_content=_has_third_party_stream,
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
        temperature: float | None = None,
        assistant_prefix: str | None = None,
    ) -> str:
        temp = temperature if temperature is not None else 0.7
        model = model_override or self.model
        if model.startswith("claude-"):
            return self._chat_anthropic(system_prompt, user_message, temperature=temp)
        if model.startswith("gpt-"):
            return self._chat_openai(system_prompt, user_message, temperature=temp)
        return self._chat_ollama(system_prompt, user_message, image_data, model, temperature=temp, assistant_prefix=assistant_prefix)

    def _chat_stream(
        self,
        system_prompt: str,
        user_message: str,
        image_data: list[str] | None = None,
        model_override: str | None = None,
        temperature: float | None = None,
        assistant_prefix: str | None = None,
    ):
        """Stream chat response. Dispatches to Ollama, Anthropic, or OpenAI."""
        temp = temperature if temperature is not None else 0.7
        model = model_override or self.model
        if model.startswith("claude-"):
            yield from self._chat_anthropic_stream(system_prompt, user_message, temperature=temp)
        elif model.startswith("gpt-"):
            yield from self._chat_openai_stream(system_prompt, user_message, temperature=temp)
        else:
            yield from self._chat_ollama_stream(system_prompt, user_message, image_data, model, temperature=temp, assistant_prefix=assistant_prefix)

    # ------------------------------------------------------------------
    # Ollama (local)
    # ------------------------------------------------------------------

    def _get_num_ctx(self) -> int:
        """Read context_length from user preferences. Clamped to [2048, 131072]."""
        from src.core.preferences import get as get_pref
        val = get_pref("context_length", 8192)
        try:
            val = int(val)
        except (TypeError, ValueError):
            val = 8192
        return max(2048, min(131072, val))

    def _chat_ollama(
        self, system_prompt: str, user_message: str,
        image_data: list[str] | None = None, model: str | None = None,
        temperature: float = 0.7,
        assistant_prefix: str | None = None,
    ) -> str:
        model = model or self.model
        user_msg: dict = {"role": "user", "content": user_message}
        if image_data:
            user_msg["images"] = image_data

        messages = [
            {"role": "system", "content": system_prompt},
            user_msg,
        ]
        # Assistant prefill: inject a partial assistant message so the
        # model continues from a grounded prefix rather than starting
        # fresh. Prevents RLHF refusal patterns from winning the first-
        # token distribution on web-search-grounded turns. The prefix
        # content is prepended to the model's output in the return value.
        if assistant_prefix:
            messages.append({"role": "assistant", "content": assistant_prefix})

        response = ollama.chat(
            model=model,
            messages=messages,
            options={"temperature": temperature, "num_ctx": self._get_num_ctx()},
        )
        generated = response["message"]["content"]
        if assistant_prefix:
            return assistant_prefix + generated
        return generated

    def _chat_ollama_stream(
        self, system_prompt: str, user_message: str,
        image_data: list[str] | None = None, model: str | None = None,
        temperature: float = 0.7,
        assistant_prefix: str | None = None,
    ):
        """Stream from Ollama. Yields string chunks."""
        model = model or self.model
        user_msg: dict = {"role": "user", "content": user_message}
        if image_data:
            user_msg["images"] = image_data

        messages = [
            {"role": "system", "content": system_prompt},
            user_msg,
        ]
        if assistant_prefix:
            messages.append({"role": "assistant", "content": assistant_prefix})
            yield assistant_prefix

        stream = ollama.chat(
            model=model,
            messages=messages,
            options={"temperature": temperature, "num_ctx": self._get_num_ctx()},
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

        logger.info("[CLOUD] Anthropic %s - non-streaming", self.model)

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

        logger.info("[CLOUD] Anthropic %s - streaming", self.model)

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

        logger.info("[CLOUD] OpenAI %s - non-streaming", self.model)

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

        logger.info("[CLOUD] OpenAI %s - streaming", self.model)

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
