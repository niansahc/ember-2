"""
src/llm/refusal.py

Refusal markers shared by the chat paths (to skip the coaching filter on
responses that intentionally end short) and the post-gen pipeline (to skip
the web-search-unavailable disclosure). One list so the two cannot drift.
"""

from __future__ import annotations

REFUSAL_PATTERNS: tuple[str, ...] = (
    "i can't help with that",
    "i'm not going to do that",
    "that's not something i'm going to do",
    "i had trouble generating a response",
)


def is_refusal_response(text: str) -> bool:
    """True if `text` looks like a refusal."""
    if not text:
        return False
    lowered = text.lower()
    return any(m in lowered for m in REFUSAL_PATTERNS)
