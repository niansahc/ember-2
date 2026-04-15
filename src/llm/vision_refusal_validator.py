"""
src/llm/vision_refusal_validator.py

Post-generation validator for the vision refusal RLHF override.

Problem: vision preprocessing generates a text description of the uploaded
image and injects it into the prompt as <vision_context>. Despite the
description being present, the primary model (qwen3:8b) sometimes emits
a canned RLHF refusal ("I can't see images directly, try tools like
ImagePrompt.org or DescribeImage.ai") — often with fabricated image-
analysis tool names. UAT-120 failed this way.

Fix: if vision preprocessing ran (used_vision=True) and the response
contains a known refusal pattern or a fabricated image-tool name,
substitute a scripted response that leans on the vision description.
The primary model's canned refusal is discarded entirely — it is a
trained-in wrong answer in this context.
"""

from __future__ import annotations

import re

_MAX_DESCRIPTION_PREFIX = 400

_REFUSAL_PATTERNS = [
    re.compile(r"\b(i\s+can\s*not|i\s+can't|i\s+cannot)\s+see\b", re.IGNORECASE),
    re.compile(r"\bi(?:'m| am)\s+unable\s+to\s+see\b", re.IGNORECASE),
    re.compile(r"\bi\s+don(?:'|)t\s+have\s+(?:the\s+)?ability\s+to\s+see\b", re.IGNORECASE),
    re.compile(r"\bi\s+lack\s+(?:the\s+)?ability\s+to\s+(?:see|view|process)\s+image", re.IGNORECASE),
    re.compile(r"\buse\s+(?:an\s+)?image[-\s]?analysis\s+tool\b", re.IGNORECASE),
    re.compile(r"\buse\s+(?:a\s+)?vision\s+tool\b", re.IGNORECASE),
    re.compile(r"\btry\s+tools\s+like\b[^\.]*\b(?:image|vision|photo)\b", re.IGNORECASE),
    re.compile(r"\btools\s+such\s+as\b[^\.]*\b(?:image|vision|photo)\b", re.IGNORECASE),
]

# Known fabricated tool names observed in UAT output. Not exhaustive —
# the refusal patterns above are the primary defence. This list is a
# safety net for the specific fabrications seen so far.
_FABRICATED_TOOL_NAMES = (
    "imageprompt.org",
    "describeimage.ai",
    "imagetotext",
    "gptimageanalyzer",
    "imageanalyzer.ai",
    "describeai",
)


def _matches_refusal(response: str) -> bool:
    lowered = response.lower()
    for name in _FABRICATED_TOOL_NAMES:
        if name in lowered:
            return True
    for pattern in _REFUSAL_PATTERNS:
        if pattern.search(response):
            return True
    return False


def _build_substitution(vision_description: str | None) -> str:
    if vision_description:
        trimmed = vision_description.strip()
        if len(trimmed) > _MAX_DESCRIPTION_PREFIX:
            trimmed = trimmed[:_MAX_DESCRIPTION_PREFIX].rstrip() + "…"
        return f"Looking at the image: {trimmed}"
    return (
        "I can see the image you shared, but I'm having trouble describing it "
        "clearly. Can you tell me what you want to know about it?"
    )


def validate_vision_response(
    response: str,
    used_vision: bool,
    vision_description: str | None = None,
) -> tuple[str, bool]:
    """Substitute the response if vision fired and the model refused anyway.

    Returns (final_response, was_substituted). Does nothing when
    used_vision is False — text-only turns are not in scope.
    """
    if not used_vision:
        return response, False
    if not response or not response.strip():
        # Let the empty-response guard handle this case.
        return response, False
    if not _matches_refusal(response):
        return response, False
    return _build_substitution(vision_description), True
