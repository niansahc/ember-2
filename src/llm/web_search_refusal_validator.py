"""
src/llm/web_search_refusal_validator.py

Deterministic fallback when the model receives web search results but
generates a refusal pattern anyway. This is the last line of defense
after assistant prefill — if the prefix injection fails to prevent
the RLHF refusal, this validator builds a response directly from the
web_search_results data structure, bypassing the LLM entirely.

The response is templated to match Ember's terse register rather than
reading like a raw search engine dump. Facts first, sources as
validation. The user never clicks a link to get the answer.
"""

from __future__ import annotations

import re

_REFUSAL_PATTERNS = [
    re.compile(r"\bi don(?:'|)t have (?:real[- ]?time|current|live|up[- ]?to[- ]?date)", re.IGNORECASE),
    re.compile(r"\bi can(?:'|)t (?:perform|do|execute|run)?\s*search", re.IGNORECASE),
    re.compile(r"\bi (?:don(?:'|)t|lack) (?:the )?(?:ability|access) to (?:search|browse|access)", re.IGNORECASE),
    re.compile(r"\byou (?:might|could|can|should) (?:check|visit|try|look at)\b", re.IGNORECASE),
    re.compile(r"\bcheck (?:out )?\b(?:CNN|Google|BBC|AP News|Reuters|Yahoo)\b", re.IGNORECASE),
    re.compile(r"\bfor (?:the )?(?:latest|most recent|current|precise|up-to-date) (?:data|info|information|details|figures)", re.IGNORECASE),
    re.compile(r"\bI (?:can(?:'t)?|don't have access to) (?:browse|access|retrieve) (?:the )?(?:web|internet|live data)", re.IGNORECASE),
    re.compile(r"\bofficial sources like\b", re.IGNORECASE),
    re.compile(r"\bhelp you (?:structure|refine|formulate) a (?:search )?query\b", re.IGNORECASE),
    re.compile(r"\bwant me to search\b", re.IGNORECASE),
    re.compile(r"\bshould i search\b", re.IGNORECASE),
    re.compile(r"\bshall i search\b", re.IGNORECASE),
]


def _has_web_refusal(response: str) -> bool:
    for pattern in _REFUSAL_PATTERNS:
        if pattern.search(response):
            return True
    return False


def _build_from_snippets(web_items: list) -> str:
    """Build a direct answer from web search snippets.

    Pulls the first sentence from each snippet (up to 3 items), joins
    them naturally, and appends source URLs. Matches Ember's terse
    register — facts first, sources as validation.
    """
    if not web_items:
        return ""

    facts = []
    sources = []
    for item in web_items[:3]:
        if not isinstance(item, dict):
            continue
        snippet = item.get("snippet", "").strip()
        url = item.get("url", "").strip()
        title = item.get("title", "").strip()
        if snippet:
            # First sentence or first 200 chars, whichever is shorter
            first_sentence = snippet.split(". ")[0].rstrip(".")
            if first_sentence and len(first_sentence) > 10:
                facts.append(first_sentence + ".")
        if url:
            sources.append(url)
        elif title:
            sources.append(title)

    if not facts:
        return ""

    response = " ".join(facts)
    if sources:
        response += "\n\nSources: " + " · ".join(sources[:3])
    return response


def validate_web_search_response(
    response: str,
    web_items: list | None,
) -> tuple[str, bool]:
    """Detect web search refusal and substitute a deterministic answer.

    Only fires when web_items is non-empty (search actually executed
    and returned results) AND the response contains a refusal pattern.

    Returns (final_response, was_substituted).
    """
    if not web_items:
        return response, False
    if not response or not response.strip():
        # Empty response handled by empty-response guard
        return response, False
    if not _has_web_refusal(response):
        return response, False

    fallback = _build_from_snippets(web_items)
    if not fallback:
        return response, False

    return fallback, True
