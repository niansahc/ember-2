"""
src/llm/ask_first_validator.py

Ember's search offers (ADR-048): the fixed texts the code uses when it offers
to search the web, and the detection of offers the model writes itself.

Problem: when the classifier routes a query to web_search intent AND
web_search_autonomous=False, the prompt instructs the model to ask
"want me to search?" before attempting an answer. At qwen3:8b scale,
the model frequently ignores this instruction and reaches for a canned
RLHF refusal ("I don't have access to live financial data, check Yahoo
Finance / Google Finance"), sometimes with fabricated source citations.
UAT-130 and UAT-131 both failed this way.

Fix: on an ask-first exchange the reply is always the code's offer,
SCRIPTED_ASK_FIRST_RESPONSE (src/llm/post_gen_pipeline substitutes it). The
model's draft is discarded: it may be a trained-in wrong answer, and its own
offer wording could leave the user unsure that the search goes to the web.
A constitutional refusal is kept and gets no offer.

First-person guard (v0.18.0): the intent classifier occasionally misroutes
clearly first-person/conversational queries to web_search (B-CTX-001
turns 7/9/10 surfaced this: recall queries hitting Stage 2 false-
positive on needs_internet exemplars). has_first_person_marker defines
the guard; src/api/openai_adapter applies it to the ask-first decision, so
such an exchange is not an ask-first exchange at all: no ask block in the
prompt, no fixed offer, and the model answers.

Search offers come only from Ember's code. RETRY_OFFER_RESPONSE and
REPEAT_SEARCH_OFFER are the repeat-search offers after a failed search. The
adapter writes a pending confirmation for the code's offers and nothing
else. OFFER_PATTERNS detects the offers the model writes itself; the
post-generation guard, remove_unbacked_offers, removes them on an exchange
where the code made no offer, since nothing would honour the user's "yes"
to them.
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

# The reply when the guard removed the model's offer and nothing else was left.
UNBACKED_OFFER_FALLBACK = "I don't have that information."

# First-person markers: pronouns and contractions that indicate the user
# is asking about themselves or the conversation. When present, we treat
# the query as personal/conversational regardless of classifier output,
# so the exchange is not ask-first. Pattern is case-insensitive and
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


# What a search offer offers to do. "search" takes any object; "look" and
# "find" count only with a web object ("look it up", "look up X online",
# "find out X online"); a retry names the search.
_WEB_LOOKUP = (
    r"(?:search"
    r"|look\s+(?:it|that|this|them|those|these)\s+up"
    r"|look\s+up\b[^.!?]*?\b(?:online|on\s+the\s+(?:web|internet))"
    r"|look\s+(?:online|on\s+the\s+(?:web|internet))"
    r"|find\s+out\b[^.!?]*?\b(?:online|on\s+the\s+(?:web|internet)|by\s+searching)"
    r"|try\s+(?:the\s+search|searching)\s+again)\b"
)

# Phrasings of an offer to search, as the model writes them. They decide
# nothing on their own: remove_unbacked_offers uses them to find the model's
# own offers and remove them on an exchange where the code made no offer
# (ADR-048). Case-insensitive, matched after curly apostrophes are
# straightened. The "?" check is loose because the model sometimes drops
# terminal punctuation, except for the "I can / I could" forms: those need a
# question mark or an "if you like / want" in the same sentence, so a
# statement such as "I can search but I won't." is not an offer.
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

# Sentence split after terminal punctuation. The separators are captured, so
# split() alternates sentence, separator, sentence, ...
_SENTENCE_SPLIT = re.compile(r"((?<=[.!?])\s+)")


def _straighten_apostrophes(text: str) -> str:
    """Curly single quotes to "'" (mobile keyboards send U+2019)."""
    return text.replace("\u2018", "'").replace("\u2019", "'")


def _is_offer_sentence(sentence: str) -> bool:
    """True when one sentence offers a search (see OFFER_PATTERNS)."""
    sentence = _straighten_apostrophes(sentence)
    return any(pattern.search(sentence) for pattern in OFFER_PATTERNS)


def remove_unbacked_offers(reply: str) -> tuple[str, int]:
    """Remove the search offers the model wrote itself (ADR-048).

    Returns (reply, number of offer sentences removed). The rest of the reply
    is kept as written. When nothing else is left, the reply becomes
    UNBACKED_OFFER_FALLBACK. Callers run this only on an exchange where the
    code made no offer, and never on a refusal. No pattern spans terminal
    punctuation, so an offer anywhere in the reply is inside one sentence.
    """
    if not reply:
        return reply, 0
    parts = _SENTENCE_SPLIT.split(reply)
    kept: list[str] = []
    removed = 0
    for i in range(0, len(parts), 2):
        if _is_offer_sentence(parts[i]):
            removed += 1
            continue
        kept.append(parts[i] + (parts[i + 1] if i + 1 < len(parts) else ""))
    if not removed:
        return reply, 0
    return "".join(kept).strip() or UNBACKED_OFFER_FALLBACK, removed
