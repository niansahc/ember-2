"""
src/llm/ask_first_validator.py

Post-generation validator for ask-first mode compliance, and the single
source of truth for how Ember phrases an offer to search the web.

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

Offer phrasing (2026-10-18 review, finding 1): OFFER_PATTERNS is the one
list of offer phrasings. The validator accepts a reply as an offer on it,
the post-gen pipeline substitutes RETRY_OFFER_RESPONSE (which matches it),
and src/api/openai_adapter._write_pending_confirmation writes a pending
confirmation on it. Before this, the writer kept its own list: the retry
offer and some accepted model offers wrote no pending, so the user's
"yes" did nothing.
"""

from __future__ import annotations

import re

SCRIPTED_ASK_FIRST_RESPONSE = (
    "I don't have current information on that — want me to search?"
)

# Substituted by the post-gen pipeline when the deferred (confirmed) search
# fails for a reason a retry could fix. It is an offer like any other: it
# matches OFFER_PATTERNS, so a pending confirmation is written for it.
RETRY_OFFER_RESPONSE = (
    "I tried searching but hit an error. Want me to try again?"
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


# Every phrasing that counts as an offer to search. Case-insensitive.
# The "?" check is loose because the model sometimes drops terminal
# punctuation, except for the "I can / I could" forms: those need a
# question mark or an "if you like / want" in the same sentence, so a
# statement such as "I can search but I won't." is not an offer.
OFFER_PATTERNS: tuple[re.Pattern, ...] = (
    # Also covers "do you want me to ..." and "let me know if you want me to ...".
    re.compile(
        r"\bwant\s+me\s+to\s+(?:search|look|find|try\s+(?:the\s+search\s+)?again)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:should|shall)\s+i\s+(?:search|look)\b", re.IGNORECASE),
    re.compile(
        r"\bwould\s+you\s+like\s+me\s+to\s+(?:search|look|find|try\s+again)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bi\s+(?:can|could)\s+(?:search|look\s+(?:that|it)\s+up)\b"
        r"[^.!?]*(?:\?|\bif\s+you(?:'d)?\s+(?:like|want)\b)",
        re.IGNORECASE,
    ),
)

# Sentence split for find_offer_sentence: after terminal punctuation.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def has_search_offer(text: str) -> bool:
    """Return True if the text contains an offer to search (OFFER_PATTERNS)."""
    if not text:
        return False
    return any(pattern.search(text) for pattern in OFFER_PATTERNS)


def find_offer_sentence(text: str) -> str | None:
    """Return the sentence that carries the offer, or None when there is none.

    No pattern spans terminal punctuation, so an offer anywhere in the text
    is always inside one sentence.
    """
    if not text:
        return None
    for sentence in _SENTENCE_SPLIT.split(text):
        if has_search_offer(sentence):
            return sentence.strip()
    return None


def is_substitutable(response: str) -> bool:
    """Return True when the response lacks a confirmation question AND is
    non-empty. Empty responses are handled by the empty-response guard,
    not by this validator."""
    if not response or not response.strip():
        return False
    return not has_search_offer(response)


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
