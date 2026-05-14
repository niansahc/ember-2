"""
src/safety/self_narrative_check.py

Class-based self-narrative claim detector (B3 fix, v0.18.0).

Flags any sentence where the assistant asserts a present-tense capability
or state about her own subsystems. Audit-only - never blocks, never
rewrites, never reads the vault. The pure function `check()` returns a
flag count and positional sentence indices; the caller writes the result
into the safety_reviews log payload.

Three deliberate miss classes (see KNOWN_ISSUES.md B-NARR-001):
  1. Self-narrative claims using subjects not in the closed list
     ("the retrieval pipeline is offline") - update the subsystem list
     when new subsystems become user-facing.
  2. Bare subsystem nouns without definite article or possessive
     ("I cannot access memory") - to avoid false positives on
     legitimate technical prose.
  3. Modal-verb claims ("would be broken", "could fail") - express
     conditional possibility, not factual assertion.

Background diagnosis: docs/audits/b3_self_narrative_diagnosis.md
(commit f8da0f2). Upgrade path: v0.19.0 config/capabilities.yaml.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path


logger = logging.getLogger("ember.self_narrative")


# Identity subjects. "Ember", "the system", "my system", "this system"
# fire the non-I arm. "I" fires only when a subsystem noun co-occurs.
_NON_I_IDENTITY_SUBJECTS: tuple[str, ...] = (
    "Ember",
    "the system",
    "my system",
    "this system",
)

# Subsystem nouns. Match only when prefixed with "the" or "my".
_SUBSYSTEM_NOUNS: tuple[str, ...] = (
    "search functionality",
    "web search",
    "search",
    "vault retrieval",
    "memory",
    "vault",
    "model",
    "API",
    "constitutional review",
    "coaching filter",
)

# Closed verb set: present-tense capability/state copulas.
# No modal verbs (would/should/could/might).
_VERB_SET: frozenset[str] = frozenset({
    "is", "am", "are",
    "can", "cannot", "can't",
    "do", "does", "don't", "doesn't",
    "won't", "will not", "will",
    "have", "has", "haven't", "hasn't",
    "am not", "isn't", "aren't",
})

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

# Build a regex alternation for verbs. Use word boundaries on each side
# so "isn't" matches but "this" doesn't match on "is". Multi-word verbs
# (will not, am not) need their own group.
_VERB_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(v) for v in sorted(_VERB_SET, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _subject_patterns() -> list[re.Pattern]:
    """Build the case-insensitive subject regex patterns.

    Non-I identity subjects match as whole words. Subsystem nouns match
    only when preceded by "the" or "my" (definite article or possessive).
    """
    patterns: list[re.Pattern] = []
    # Non-I identity subjects.
    for subj in _NON_I_IDENTITY_SUBJECTS:
        patterns.append(re.compile(r"\b" + re.escape(subj) + r"\b", re.IGNORECASE))
    # Subsystem nouns with definite article or possessive.
    # Longer nouns first so "search functionality" matches before "search".
    nouns_by_length = sorted(_SUBSYSTEM_NOUNS, key=len, reverse=True)
    for noun in nouns_by_length:
        patterns.append(
            re.compile(r"\b(?:the|my)\s+" + re.escape(noun) + r"\b", re.IGNORECASE),
        )
    return patterns


_SUBJECT_PATTERNS: list[re.Pattern] = _subject_patterns()

# "I" subject regex with word boundary. Case-sensitive so we do not
# match "i" inside other words.
_I_SUBJECT_RE = re.compile(r"\bI\b")

# "the X" / "my X" for any subsystem noun, used as the co-occurrence
# check on the I-arm.
_SUBSYSTEM_CO_OCCURRENCE_RE = re.compile(
    r"\b(?:the|my)\s+(?:"
    + "|".join(re.escape(n) for n in sorted(_SUBSYSTEM_NOUNS, key=len, reverse=True))
    + r")\b",
    re.IGNORECASE,
)


def _sentence_matches(sentence: str) -> bool:
    """Return True if the sentence matches any self-narrative rule.

    Non-I arm: any non-I identity or subsystem-with-article subject
    appears AND a closed-set verb appears.
    I arm: "I" appears AND a verb appears AND a subsystem-with-article
    noun co-occurs.
    """
    has_verb = bool(_VERB_RE.search(sentence))
    if not has_verb:
        return False
    # Non-I arm: any non-I subject present.
    for pat in _SUBJECT_PATTERNS:
        if pat.search(sentence):
            return True
    # I arm: "I" subject + subsystem co-occurrence.
    if _I_SUBJECT_RE.search(sentence) and _SUBSYSTEM_CO_OCCURRENCE_RE.search(sentence):
        return True
    return False


def check(response_text: str) -> tuple[int, list[int]]:
    """Return (flag_count, sentence_indices) for self-narrative claims.

    Pure function. No I/O. No vault read. No log write. Indices are
    0-indexed positions within a naive sentence split of `response_text`.
    """
    if not response_text or not response_text.strip():
        return (0, [])
    sentences = _SENTENCE_SPLIT.split(response_text.strip())
    indices = [i for i, s in enumerate(sentences) if _sentence_matches(s)]
    return (len(indices), indices)


def log_self_narrative_outcome(
    flag_count: int,
    sentence_indices: list[int],
    intent_class: str | None = None,
) -> None:
    """Write a self_narrative_check entry to logs/safety_reviews/.

    Only writes when flag_count > 0 (Option B lifecycle). The entry
    carries timestamp, type, flag_count, sentence_indices (positional
    metadata only), and optional intent_class. No response text or
    sentence content is ever included.

    Side-effect only - no return value. Non-fatal on errors.
    """
    if flag_count <= 0:
        return
    try:
        log_dir = Path(__file__).resolve().parents[2] / "logs" / "safety_reviews"
        log_dir.mkdir(parents=True, exist_ok=True)
        entry: dict = {
            "timestamp": datetime.now().isoformat(),
            "type": "self_narrative_check",
            "flag_count": flag_count,
            "sentence_indices": list(sentence_indices),
        }
        if intent_class is not None:
            entry["intent_class"] = intent_class
        log_file = (
            log_dir
            / f"{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}Z-self_narrative.json"
        )
        log_file.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("[SELF_NARRATIVE] Failed to log outcome: %s", exc)
