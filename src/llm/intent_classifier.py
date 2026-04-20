"""
src/llm/intent_classifier.py

Three-tier intent cascade for ask-first routing (ADR-034).

Classifies each user query as either:
  - needs_internet   : query requires current/external information
  - vault_answerable : query should be answered from the user's personal vault

Architecture (ADR-034):
  Stage 1  Structural rules with compound first-person guard   ~2ms     (this file, commit 1)
  Stage 2  Embedding similarity against labeled examples       ~30-50ms (commit 2)
  Stage 3  qwen3:8b non-thinking with JSON grammar, 800ms cap  ~300-800ms (commit 3)

Only Stage 1 is active in this commit. Stages 2 and 3 are added in
subsequent commits on the same branch. Until then, Stage 1 escalation
falls through to the safe default: "vault_answerable". This matches the
ADR-mandated timeout fallback for Stage 3, so the conservative default
is consistent across the cascade.

TODO: SetFit upgrade when 150 labels/class accumulated from logs.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("ember.intent_classifier")


# ---------------------------------------------------------------------------
# Stage 1: Structural rules with compound first-person guard
# ---------------------------------------------------------------------------
# The current keyword classifier in src/context/policies.py fails on
# first-person queries containing volatile-sounding words. Stage 1 catches
# the clearest internet-only signals (weather, stock price, headlines,
# live scores) and applies a compound guard: a definite internet signal
# only triggers when the query does NOT have a first-person marker
# WITHOUT an external-world anchor. See ADR-034 Stage 1 for the full
# rationale.

DEFINITE_INTERNET_SIGNALS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(weather|forecast|temperature)\b",
        r"\b(stock|share|crypto|bitcoin|eth)\s+price\b",
        r"\b(today'?s?|current|latest)\s+(news|headlines|updates)\b",
        r"\b(live\s+score|standings|who\s+won)\b",
    )
)

FIRST_PERSON_MARKERS = re.compile(
    r"\b(my|mine|I'm|I've|I\s+am|I\s+was|I\s+have|I\s+had|I\s+said|I\s+mentioned)\b",
    re.IGNORECASE,
)

EXTERNAL_WORLD_ANCHORS = re.compile(
    r"\b(weather|price|news|election|score|market|stock|"
    r"currently\s+in\s+the\s+news|headlines)\b",
    re.IGNORECASE,
)


def _stage1_classify(query: str) -> str | None:
    """Stage 1: compound structural rules.

    Returns "needs_internet" or "vault_answerable" when confident; None
    when the query should escalate to Stage 2.
    """
    if not query:
        return None

    has_first_person = bool(FIRST_PERSON_MARKERS.search(query))
    has_external_anchor = bool(EXTERNAL_WORLD_ANCHORS.search(query))

    for signal in DEFINITE_INTERNET_SIGNALS:
        if signal.search(query):
            # Compound guard: block the internet signal only when first-person
            # is present AND there's no external-world anchor. Without this
            # guard, "I'm currently watching the news" would route to internet
            # because of the news signal, even though it's a personal statement.
            if has_first_person and not has_external_anchor:
                return "vault_answerable"
            return "needs_internet"
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Safe default applied at every escalation path in this commit. Matches
# ADR-034 Stage 3 timeout fallback: when uncertain, treat as vault-answerable
# and let the user explicitly request a search if they want one.
_SAFE_DEFAULT: str = "vault_answerable"


def classify_intent(query: str) -> str:
    """Classify a user query as needs_internet or vault_answerable.

    Runs the active stages of the ADR-034 cascade. Always returns one of
    the two valid labels — never raises, never returns None. Emits exactly
    one [INTENT_CLASSIFY] log line per call for the training-data pipeline
    described in ADR-034 Upgrade Path.
    """
    stage1 = _stage1_classify(query)
    if stage1 is not None:
        _log(stage="stage1", label=stage1, confidence=None, query=query)
        return stage1

    # Stages 2 and 3 not yet implemented — escalate to safe default.
    _log(stage="fallback", label=_SAFE_DEFAULT, confidence=None, query=query)
    return _SAFE_DEFAULT


def _log(stage: str, label: str, confidence: float | None, query: str) -> None:
    """Emit the structured classification log line.

    ASCII-only to avoid Windows cp1252 corruption (CLAUDE.md rule 7).
    Query is truncated to 200 chars per ADR-034 logging spec.
    """
    logger.info(
        "[INTENT_CLASSIFY] stage=%s label=%s confidence=%s query=%s",
        stage,
        label,
        "none" if confidence is None else f"{confidence:.3f}",
        query[:200],
    )
