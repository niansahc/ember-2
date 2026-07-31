"""
src/llm/vision_service.py

Vision preprocessor service. Runs a vision-capable model (default: qwen3-vl:8b)
to extract a text description from user-uploaded images BEFORE the main LLM call.
The description is injected into the context packet as a <vision_context> section,
so the primary reasoning model can reference image content without needing vision
capabilities itself.

This is distinct from the legacy vision path in LLMAdapter, which sends images
directly to a vision model as the primary responder. The preprocessor pattern
decouples image understanding from response generation, allowing the full
prompt pipeline (context assembly, identity rules, constitutional review) to
run on the text description rather than being bypassed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ollama

logger = logging.getLogger("ember.vision")

# Hardcoded fallback vision model. The runtime resolution order in
# VisionService.__init__ is: explicit constructor arg → EMBER_VISION_MODEL
# env var (via get_ember_vision_model()) → this fallback. Only reached when
# both higher-priority sources are unset.
DEFAULT_VISION_MODEL = "qwen3-vl:8b"

# Vision analysis prompt — task-specific instructions for the vision model.
# For images containing text, code, or error messages: extract and quote verbatim.
# For general images: describe conversationally.
VISION_PROMPT = (
    "Analyze the image(s) provided. Follow these rules:\n"
    "- If the image contains text, code, error messages, or screenshots with "
    "readable content, extract and quote the text verbatim. Preserve formatting.\n"
    "- If the image is a general photograph, diagram, or illustration, describe "
    "what you see conversationally in 2-4 sentences.\n"
    "- Be specific about what is visible. Do not speculate about what is not shown.\n"
    "- If multiple images are provided, describe each one separately."
)

# Maximum tokens for vision model output. Keeps preprocessing fast.
VISION_MAX_TOKENS = 300

# Emitted when images are present but VL preprocessing did not produce a
# description (issue #130). Same trust class as SCRIPTED_CLARIFICATION_RESPONSE
# in src/context/policies.py: a fixed string, never model-generated, so it
# cannot itself be a hallucination.
#
# States the cause and the scope and stops. No apology, no closing question,
# no offer to try again - the failure is environmental and retrying the same
# turn will not clear it.
VISION_UNAVAILABLE_RESPONSE: str = (
    "I can't read that image right now. The vision model failed to load, "
    "so image analysis is unavailable. Text conversation still works."
)

# JSON-lines structured log directory. Mirrors the pattern used by the
# audit log and safety_reviews/coaching_filter logs — repo-local, one file
# per UTC day. Diagnoses vision pipeline activity that the HTTP audit log
# can't reach (failures inside analyze(), empty-input early returns, etc.).
_LOG_DIR = Path(__file__).resolve().parents[2] / "logs" / "vision"


def _log_vision(event: str, **fields: Any) -> None:
    """Append a JSON line to logs/vision/YYYY-MM-DD.log. Never raises —
    vision must continue to function even if the log volume is full or
    the directory is read-only."""
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            **fields,
        }
        log_file = _LOG_DIR / f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"
        with log_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("[VISION_LOG] Write failed (non-fatal): %s", exc)


class VisionService:
    """Preprocessor that converts images to text descriptions via a vision model.

    The analyze() method sends base64 image data to the configured vision model
    and returns a text description. This description is then injected into the
    prompt as a <vision_context> section by the prompt builder.
    """

    def __init__(self, model: str | None = None) -> None:
        if model is None:
            from src.core.config import get_ember_vision_model
            model = get_ember_vision_model()
        self.model = model or DEFAULT_VISION_MODEL

    def analyze(self, image_data: list[str]) -> str:
        """Analyze one or more images and return a text description.

        Args:
            image_data: List of base64-encoded image strings (no data URL prefix).

        Returns:
            Text description of the image content. Empty string if analysis fails
            or no images are provided.
        """
        if not image_data:
            _log_vision("vision_empty_input")
            return ""

        _log_vision(
            "vision_entry",
            model=self.model,
            image_count=len(image_data),
        )

        try:
            _log_vision(
                "vision_ollama_call",
                model=self.model,
                num_predict=VISION_MAX_TOKENS,
            )
            response = ollama.chat(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": VISION_PROMPT,
                        "images": image_data,
                    },
                ],
                options={
                    "temperature": 0.3,
                    "num_predict": VISION_MAX_TOKENS,
                },
            )
            description = response.get("message", {}).get("content", "")
            if description:
                logger.info(
                    "[VISION] Preprocessor produced %d chars from %d image(s)",
                    len(description),
                    len(image_data),
                )
                _log_vision(
                    "vision_success",
                    description_chars=len(description),
                    description_preview=description[:80],
                )
            else:
                _log_vision(
                    "vision_success",
                    description_chars=0,
                    description_preview="",
                )
            return description.strip()
        except Exception as exc:
            logger.warning("[VISION] Preprocessor failed (non-fatal): %s", exc)
            _log_vision(
                "vision_failure",
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )
            return ""
