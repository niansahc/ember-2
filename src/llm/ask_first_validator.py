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

First-person guard (v0.18.0): the intent classifier occasionally misroutes
clearly first-person/conversational queries to web_search (B-CTX-001
turns 7/9/10 surfaced this — recall queries hitting Stage 2 false-
positive on needs_internet exemplars). When the user_message contains a
first-person marker, we skip the substitution and let the model's draft
through. The downstream coaching filter still trims any RLHF residue.
"""

from __future__ import annotations

import re

SCRIPTED_ASK_FIRST_RESPONSE = (
    "I don't have current information on that — want me to search?"
)

# First-person markers: pronouns and contractions that indicate the user
# is asking about themselves or the conversation. When present, we treat
# the query as personal/conversational regardless of classifier output
# and skip the ask-first substitution. Pattern is case-insensitive and
# word-bounded so common-word substrings ("welcome", "remembered") do
# not false-match.
#
# 'me' and 'us' are intentionally NOT included: in English they are
# routinely dative-object pronouns in imperative constructions
# ("tell me about X", "show us how Y works") where the speaker is
# asking the system to act on an external topic, not about themselves.
# Possessives ('my', 'our'), subjects ('I', 'we'), reflexives ('myself',
# 'ourselves'), and contractions are unambiguous first-person markers.
_FIRST_PERSON_GUARD = re.compile(
    r"\b(I|my|mine|myself|i'?m|i'?ve|i'?d|i'?ll|"
    r"we|our|ours|ourselves|we'?re|we'?ve|we'?d|we'?ll)\b",
    re.IGNORECASE,
)


def _has_first_person_marker(user_message: str) -> bool:
    """Return True if the user message contains a first-person marker."""
    if not user_message:
        return False
    return bool(_FIRST_PERSON_GUARD.search(user_message))

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
    user_message: str = "",
) -> tuple[str, bool]:
    """Enforce the ask-first confirmation pattern.

    Returns (final_response, was_substituted). Substitution happens only
    when all of: intent_class == "web_search", ask_first_mode is True,
    the response lacks any recognised confirmation question, AND the
    user_message lacks a first-person marker. Empty responses pass
    through unchanged — the empty-response guard runs separately.

    The first-person guard protects the user from receiving a canned
    "want me to search?" response on conversational/recall queries that
    the classifier mistakenly routed to web_search (B-CTX-001 family).
    When the guard fires, the model's actual draft is preserved; any
    RLHF residue is handled by the coaching filter downstream.
    """
    if intent_class != "web_search":
        return response, False
    if not ask_first_mode:
        return response, False
    if _has_first_person_marker(user_message):
        return response, False
    if not is_substitutable(response):
        return response, False
    return SCRIPTED_ASK_FIRST_RESPONSE, True
