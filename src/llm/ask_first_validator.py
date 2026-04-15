"""
src/llm/ask_first_validator.py

Post-generation validator for ask-first mode compliance.

Problem: when the classifier routes a query to web_search intent AND
web_search_autonomous=False, the prompt instructs the model to ask
"want me to search?" before attempting an answer. At qwen3:8b scale,
the model frequently ignores this instruction and reaches for a canned
RLHF refusal ("I don't have access to live financial data, check Yahoo
Finance / Google Finance") — sometimes with fabricated source citations.
UAT-130 and UAT-131 both failed this way.

Fix: if ask-first was instructed but the response lacks an explicit
search-confirmation question, substitute a scripted response that asks.
The original response is discarded — it's a trained-in wrong answer and
keeping any of it risks carrying the RLHF refusal through.
"""

from __future__ import annotations

import re

SCRIPTED_ASK_FIRST_RESPONSE = (
    "I don't have current information on that — want me to search?"
)

# Confirmation patterns. Case-insensitive. The "?" check is loose because
# the model sometimes drops terminal punctuation.
_CONFIRMATION_PATTERNS = [
    re.compile(r"\bwant\s+me\s+to\s+search\b", re.IGNORECASE),
    re.compile(r"\bshould\s+i\s+search\b", re.IGNORECASE),
    re.compile(r"\bshall\s+i\s+search\b", re.IGNORECASE),
    re.compile(r"\bwould\s+you\s+like\s+me\s+to\s+search\b", re.IGNORECASE),
    re.compile(r"\bdo\s+you\s+want\s+me\s+to\s+search\b", re.IGNORECASE),
    re.compile(r"\blet\s+me\s+know\s+if\s+you\s+want\s+me\s+to\s+search\b", re.IGNORECASE),
    # "I can search for that" etc. — require a nearby question mark to
    # avoid matching statements like "I can search but I won't."
    re.compile(r"\bi\s+can\s+search\b[^\.\!]*\?", re.IGNORECASE),
    re.compile(r"\bi\s+can\s+look\s+(?:that|it)\s+up\b[^\.\!]*\?", re.IGNORECASE),
]


def _has_confirmation_question(response: str) -> bool:
    """Return True if the response contains a search-confirmation pattern."""
    if not response:
        return False
    for pattern in _CONFIRMATION_PATTERNS:
        if pattern.search(response):
            return True
    return False


def is_substitutable(response: str) -> bool:
    """Return True when the response lacks a confirmation question AND is
    non-empty. Empty responses are handled by the empty-response guard,
    not by this validator."""
    if not response or not response.strip():
        return False
    return not _has_confirmation_question(response)


def validate_ask_first_response(
    response: str,
    intent_class: str,
    ask_first_mode: bool,
) -> tuple[str, bool]:
    """Enforce the ask-first confirmation pattern.

    Returns (final_response, was_substituted). Substitution happens only
    when all of: intent_class == "web_search", ask_first_mode is True,
    and the response lacks any recognised confirmation question. Empty
    responses pass through unchanged — the empty-response guard runs
    separately and will fill them.
    """
    if intent_class != "web_search":
        return response, False
    if not ask_first_mode:
        return response, False
    if not is_substitutable(response):
        return response, False
    return SCRIPTED_ASK_FIRST_RESPONSE, True
