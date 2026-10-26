"""
src/llm/refusal.py

The constitutional review's refusal, as the chat path receives it.

When the review decides refuse_redirect, LLMAdapter returns the refusal as a
RefusalText: a str whose type is the review's outcome. The chat path reads
the type once, straight after generation (is_review_refusal), and passes the
answer on as a flag: the coaching-filter skip, the grounding revision skip
and the post-gen pipeline all act on that flag, never on the wording. A
refusal is never rewritten, replaced by a search offer, or followed by a
pending confirmation (CLAUDE.md implementation standard 2, ADR-048).

The type replaced a list of refusal phrases. A real refusal is model-written
(RefusalRedirect.as_text(): reason plus safer alternative) or the review's
heuristic fallback, and neither matched the list, so a refused ask-first
exchange got the search offer in place of the refusal.
"""

from __future__ import annotations


class RefusalText(str):
    """The refusal text of a refuse_redirect review outcome. Behaves as the
    plain text; only its type says the review refused. String operations
    return plain str, so read the type before transforming the text."""

    __slots__ = ()


def is_review_refusal(text: object) -> bool:
    """True when `text` is the constitutional review's refusal."""
    return isinstance(text, RefusalText)
