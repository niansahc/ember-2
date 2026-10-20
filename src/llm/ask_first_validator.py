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
turns 7/9/10 surfaced this: recall queries hitting Stage 2 false-
positive on needs_internet exemplars). has_first_person_marker defines
the guard; src/api/openai_adapter applies it to the ask-first decision, so
such an exchange is not an ask-first exchange at all: no ask block in the
prompt, no fixed offer, and the model answers.

Search offers come only from Ember's code (ADR-048). On an ask-first
exchange the reply is always SCRIPTED_ASK_FIRST_RESPONSE, which names the
web, so the user's consent is unambiguous; a refusal is kept and gets no
offer. RETRY_OFFER_RESPONSE is the repeat-search offer the post-gen
pipeline substitutes when a confirmed search fails. The adapter writes a
pending confirmation for these offers only. OFFER_PATTERNS detects the
model's own offers (has_search_offer); nothing it matches writes a pending.
"""

from __future__ import annotations

import re

SCRIPTED_ASK_FIRST_RESPONSE = (
    "I don't have current information on that. Want me to search the web for it?"
)

# The repeat-search offers (ADR-048): the code's offer after a search failed
# for a reason a repeat could fix (engines blocked, timeout, request error).
# RETRY_OFFER_RESPONSE replaces the reply on a confirmation exchange whose
# deferred search failed; REPEAT_SEARCH_OFFER is appended to the reply after
# any other failed search (autonomous, explicit, search clarification). The
# adapter writes a pending confirmation for either. Both name the web.
RETRY_OFFER_RESPONSE = (
    "I tried searching the web but hit an error. Want me to try the search again?"
)
REPEAT_SEARCH_OFFER = "Want me to try the web search again?"

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


def has_first_person_marker(user_message: str) -> bool:
    """Return True if the user message contains a first-person marker."""
    if not user_message:
        return False
    return bool(_FIRST_PERSON_GUARD.search(user_message))


# What Ember offers to do, when the offer is a web lookup. "search" takes
# any object; "look" and "find" count only with a web object ("look it up",
# "look up X online", "find out X online"); a retry names the search.
_WEB_LOOKUP = (
    r"(?:search"
    r"|look\s+(?:it|that|this|them|those|these)\s+up"
    r"|look\s+up\b[^.!?]*?\b(?:online|on\s+the\s+(?:web|internet))"
    r"|look\s+(?:online|on\s+the\s+(?:web|internet))"
    r"|find\s+out\b[^.!?]*?\b(?:online|on\s+the\s+(?:web|internet)|by\s+searching)"
    r"|try\s+(?:the\s+search|searching)\s+again)\b"
)

# Every phrasing that counts as an offer to search. Case-insensitive, matched
# after curly apostrophes are straightened. The "?" check is loose because
# the model sometimes drops terminal punctuation, except for the "I can /
# I could" forms: those need a question mark or an "if you like / want" in
# the same sentence, so a statement such as "I can search but I won't." is
# not an offer.
OFFER_PATTERNS: tuple[re.Pattern, ...] = (
    # Also covers "do you want me to ..." and "let me know if you want me to ...".
    re.compile(r"\bwant\s+me\s+to\s+" + _WEB_LOOKUP, re.IGNORECASE),
    re.compile(r"\b(?:should|shall)\s+i\s+" + _WEB_LOOKUP, re.IGNORECASE),
    re.compile(r"\bwould\s+you\s+like\s+me\s+to\s+" + _WEB_LOOKUP, re.IGNORECASE),
    re.compile(
        r"\bi\s+(?:can|could)\s+" + _WEB_LOOKUP
        + r"[^.!?]*(?:\?|\bif\s+you(?:'d)?\s+(?:like|want)\b)",
        re.IGNORECASE,
    ),
)

# A sentence that names one of the user's own sources is about that source,
# not the web ("want me to search your notes?"), unless it also names the web.
_LOCAL_SOURCE = re.compile(
    r"\b(?:your|my|our)\s+(?:notes|files?|documents?|journal|vault|memor(?:y|ies)"
    r"|records|drafts?|essays?|messages|conversations?|history)\b",
    re.IGNORECASE,
)
_WEB_WORD = re.compile(r"\b(?:web|online|internet)\b", re.IGNORECASE)

# Sentence split for find_offer_sentence: after terminal punctuation.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _straighten_apostrophes(text: str) -> str:
    """Curly single quotes to "'" (mobile keyboards send U+2019)."""
    return text.replace("\u2018", "'").replace("\u2019", "'")


def _is_offer_sentence(sentence: str) -> bool:
    """True when one sentence offers a web lookup (see OFFER_PATTERNS)."""
    sentence = _straighten_apostrophes(sentence)
    if not any(pattern.search(sentence) for pattern in OFFER_PATTERNS):
        return False
    return not (_LOCAL_SOURCE.search(sentence) and not _WEB_WORD.search(sentence))


def find_offer_sentence(text: str) -> str | None:
    """Return the sentence that carries the offer, or None when there is none.

    No pattern spans terminal punctuation, so an offer anywhere in the text
    is always inside one sentence. The sentence is returned as written.
    """
    if not text:
        return None
    for sentence in _SENTENCE_SPLIT.split(text):
        if _is_offer_sentence(sentence):
            return sentence.strip()
    return None


def has_search_offer(text: str) -> bool:
    """Return True if the text contains an offer to search (OFFER_PATTERNS)."""
    return find_offer_sentence(text) is not None


def validate_ask_first_response(
    response: str,
    intent_class: str,
    ask_first_mode: bool,
    is_refusal: bool = False,
) -> tuple[str, bool]:
    """Put the code's offer on an ask-first exchange.

    Returns (final_response, was_substituted). On an ask-first exchange
    (intent_class == "web_search" and ask_first_mode) the reply is always
    SCRIPTED_ASK_FIRST_RESPONSE, whatever the model drafted: its own offer
    wording could leave the user unsure that the search goes to the web. A
    constitutional refusal is the one exception: it is kept as written and
    gets no offer (CLAUDE.md implementation standard 2). Anywhere else the
    response passes through unchanged.
    """
    if intent_class != "web_search" or not ask_first_mode or is_refusal:
        return response, False
    return SCRIPTED_ASK_FIRST_RESPONSE, True
