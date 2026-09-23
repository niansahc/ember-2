"""
src/context/low_value.py

Content-class detectors for material that is low value as retrieval context
or as reflection input.

Why this module exists
----------------------
Three call sites independently grew their own definition of "low value",
and all three implemented it by hardcoding verbatim user utterances taken
from one install's real usage:

  - ContextRanker._looks_like_low_value_prompt  (a -0.18 score penalty)
  - ContextService._is_low_value_memory         (a hard filter)
  - generate_reflection._should_skip_for_reflection (a skip marker list)

That violates the CLAUDE.md Vault Privacy Rule, which states with no
exceptions that conversation text must never appear in the codebase. It
also does not generalise: an exact-match list built from one person's
sentences can never fire on any other install, so the behaviour it
encodes is unavailable to every user except the one whose words were
copied into the source.

This module replaces those lists with patterns that describe the CLASS
rather than quoting an instance. Two classes are modelled:

  style feedback        the user commenting on the assistant's output
                        length or verbosity. Meta-commentary about the
                        conversation, not content about the user's life.

  assistant-meta prompt the user addressing the assistant about its own
                        perception, memory or opinion. The user's prompt
                        retrieved back as if it were evidence.

A third list, MODEL_NON_ANSWER_MARKERS, holds generic model boilerplate.
Those strings are assistant output artifacts common to every instruction
-tuned model, not user text, so they carry no privacy concern.

Both patterns are gated by MAX_META_LENGTH. Style feedback and meta
prompts are short conversational turns; the length bound is what keeps
"i wrote a long response to the design doc" -- a substantive memory that
happens to contain the pattern -- from being classified as meta. Without
it the patterns would be broad enough to drop real content, which on a
corpus where most records are already hard to reach is the more expensive
error.
"""

from __future__ import annotations

import re

from src.observability.guard_counters import count

# Meta-commentary is short by nature. A record longer than this that
# happens to match a pattern is far more likely to be substantive content
# mentioning the topic than commentary about it. 120 matches the bound
# ContextService already uses for short questions, so the two short-content
# rules agree rather than each carrying their own number.
MAX_META_LENGTH = 120

# The user describing output they wrote themselves, rather than commenting
# on the assistant's. "i spent the afternoon drafting a long response" is a
# memory about the user's day; "that was a long response" is commentary.
# Without this the style-feedback pattern cannot tell them apart, because
# both contain a length adjective next to a word for written output.
_FIRST_PERSON_AUTHORSHIP = re.compile(
    r"\bi\b\s+(?:\w+\s+){0,3}"
    r"(?:wrote|writing|drafted|drafting|sent|sending|posted|posting|typed|"
    r"composed|composing|replied|replying|answered|answering)\b",
    re.IGNORECASE,
)

# Generic instruction-tuned-model boilerplate. Not user text: these are
# assistant non-answers that carry no information about the user and are
# noise wherever they are retrieved.
MODEL_NON_ANSWER_MARKERS: tuple[str, ...] = (
    "as an ai, i don't have personal experiences",
    "as an ai, i do not have personal experiences",
    "as an ai language model",
    "i don't have access to personal information about you",
    "i do not have access to personal information about you",
)

# A length or verbosity adjective directly qualifying a word for the
# assistant's output. Up to two intervening words allows for "shorter
# bullet-point responses" without letting the two halves drift apart into
# unrelated clauses.
_STYLE_FEEDBACK_ADJECTIVE_FIRST = re.compile(
    r"\b(?:short|shorter|long|longer|brief|briefer|concise|verbose|wordy|rambling)\b"
    r"\s+(?:\w+[\s-]+){0,2}"
    r"(?:response|reply|replies|message|answer|explanation|paragraph)s?\b",
    re.IGNORECASE,
)

# The reverse order: the output word first, then the complaint. Bounded to
# one clause so it cannot span a sentence boundary.
_STYLE_FEEDBACK_NOUN_FIRST = re.compile(
    r"\b(?:response|reply|replies|message|answer|explanation|paragraph)s?\b"
    r"[^.!?]{0,30}?"
    r"\b(?:too\s+(?:long|short|verbose|wordy)|so\s+long|way\s+too\s+long)\b",
    re.IGNORECASE,
)

# The user addressing the assistant about its own perception, memory or
# opinion, in interrogative or directive form. The bounded gap keeps the
# opener and the second-person verb inside one clause.
_ASSISTANT_META_PROMPT = re.compile(
    r"\b(?:tell me|show me|what|which|how|why|do|did|can|could|would|are)\b"
    r"[^.!?]{0,60}?"
    r"\byou\b\s+(?:see|saw|know|think|thought|remember|recall|notice|noticed|"
    r"have|observe|observed|make of|feel|understand)\b",
    re.IGNORECASE,
)


def is_style_feedback(content: str) -> bool:
    """True when the content is the user commenting on response style.

    Length or verbosity feedback is about the conversation rather than
    about the user, so it is noise in a retrieved context packet and in
    reflection input alike.
    """
    if not content:
        return False
    if len(content) > MAX_META_LENGTH:
        return False
    if _FIRST_PERSON_AUTHORSHIP.search(content):
        return False
    return bool(
        _STYLE_FEEDBACK_ADJECTIVE_FIRST.search(content)
        or _STYLE_FEEDBACK_NOUN_FIRST.search(content)
    )


def is_assistant_meta_prompt(content: str) -> bool:
    """True when the content is the user asking the assistant about itself.

    These are the user's own prompts stored as conversation turns. Retrieved
    back later they read as evidence about the user while carrying none:
    the substance, if any, is in whatever followed.
    """
    if not content:
        return False
    if len(content) > MAX_META_LENGTH:
        return False
    return bool(_ASSISTANT_META_PROMPT.search(content))


def is_model_non_answer(content: str) -> bool:
    """True when the content is generic instruction-tuned-model boilerplate."""
    if not content:
        return False
    lowered = content.lower()
    return any(marker in lowered for marker in MODEL_NON_ANSWER_MARKERS)


def is_low_value_content(content: str) -> bool:
    """Combined check used by the retrieval filter and the reflection skip.

    Callers apply their own additional rules (length floors, content_kind
    gating); this covers only the three shared classes.
    """
    # Counted per class: these are three independent rules and "the
    # style-feedback pattern has never matched a real record" is a
    # different finding from "the whole filter never fires".
    return (
        count("low_value.model_non_answer", is_model_non_answer(content))
        or count("low_value.style_feedback", is_style_feedback(content))
        or count("low_value.assistant_meta_prompt", is_assistant_meta_prompt(content))
    )
