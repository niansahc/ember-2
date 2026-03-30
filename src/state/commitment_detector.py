"""
src/state/commitment_detector.py

Post-generation commitment detection (ADR-014).

Analyzes Ember's draft response for commitment language and returns a
detection result. When a commitment is detected, the caller writes an
open_loop state record so the commitment persists across turns.

Detection uses pattern matching against known commitment phrases.
Conservative threshold — false negatives are acceptable, false positives
are not. A commitment written to state that Ember never made is worse
than missing one she did make.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("ember.commitment_detector")


@dataclass
class CommitmentDetectionResult:
    detected: bool
    commitment_text: str | None = None


# Commitment patterns — phrases that indicate Ember is making a promise
# or committing to a future action. Ordered by specificity.
COMMITMENT_PATTERNS = (
    # Direct future promises
    "i'll walk you through",
    "i'll help you with",
    "i'll follow up",
    "i'll check on",
    "i'll look into",
    "i'll get back to you",
    "i'll make sure",
    "i'll keep track",
    "i'll remind you",
    "i'll put together",
    "i'll draft",
    "i'll prepare",
    "i'll send you",
    "i'll find",
    "i'll research",
    # Collaborative future plans
    "let's go through this",
    "let's work through",
    "let's tackle",
    "let's start with",
    "we'll go through",
    "we'll work on",
    "we'll come back to",
    "we can revisit",
    # Structured commitments
    "here's your plan",
    "here's your order for the day",
    "here's what we'll do",
    "here are the steps",
    "first we'll",
    "next time we",
    "when you're ready",
    # Scheduling / follow-up
    "tomorrow we",
    "next session",
    "next time",
    "i want to follow up",
    "remind me to",
)

# Non-commitment patterns — phrases that sound like commitments but are
# just offers or acknowledgments. Used as negative signals.
NON_COMMITMENT_PATTERNS = (
    "i can help with",
    "i'd be happy to",
    "feel free to",
    "let me know if",
    "if you'd like",
    "i could",
    "you might want to",
    "that sounds",
    "here's some information",
)


def detect_commitment(response_text: str) -> CommitmentDetectionResult:
    """
    Analyze Ember's response for commitment language.

    Returns a CommitmentDetectionResult with detected=True if a commitment
    pattern is found and no strong non-commitment signal overrides it.

    The commitment_text is extracted as the sentence containing the match,
    truncated to 120 characters.
    """
    if not response_text or len(response_text.strip()) < 20:
        return CommitmentDetectionResult(detected=False)

    lower = response_text.lower()

    # Check for non-commitment patterns first — if the response is purely
    # an offer or acknowledgment, skip even if commitment words appear
    non_commitment_count = sum(1 for p in NON_COMMITMENT_PATTERNS if p in lower)

    for pattern in COMMITMENT_PATTERNS:
        if pattern in lower:
            # If there are more non-commitment signals than commitment signals,
            # this is probably an offer, not a commitment
            if non_commitment_count > 1:
                logger.info("[COMMITMENT] Pattern '%s' found but overridden by %d non-commitment signals", pattern, non_commitment_count)
                continue

            # Extract the sentence containing the commitment
            commitment_text = _extract_sentence(response_text, pattern)
            logger.info("[COMMITMENT] Detected: %s", commitment_text[:80])

            return CommitmentDetectionResult(
                detected=True,
                commitment_text=commitment_text,
            )

    return CommitmentDetectionResult(detected=False)


def _extract_sentence(text: str, pattern: str) -> str:
    """Extract the sentence containing the pattern, truncated to 120 chars."""
    lower = text.lower()
    idx = lower.find(pattern)
    if idx == -1:
        return text[:120]

    # Walk backward to find sentence start
    start = idx
    while start > 0 and text[start - 1] not in '.!?\n':
        start -= 1

    # Walk forward to find sentence end
    end = idx + len(pattern)
    while end < len(text) and text[end] not in '.!?\n':
        end += 1
    if end < len(text):
        end += 1  # include the punctuation

    sentence = text[start:end].strip()
    if len(sentence) > 120:
        sentence = sentence[:117] + '...'
    return sentence
