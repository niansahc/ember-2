"""
src/llm/intent_classifier.py

Three-tier intent cascade for ask-first routing (ADR-034).

Classifies each user query as either:
  - needs_internet   : query requires current/external information
  - vault_answerable : query should be answered from the user's personal vault

Architecture (ADR-034):
  Stage 1  Structural rules with compound first-person guard   ~2ms     (commit 1)
  Stage 2  Embedding similarity against labeled examples       ~30-50ms (commit 2)
  Stage 3  qwen3:8b non-thinking with JSON grammar, 800ms cap  ~300-800ms (commit 3)

Only Stages 1 and 2 are active in this commit. Stage 3 lands in the next
commit on the same branch. Until then, escalation from Stage 2 falls
through to the safe default: "vault_answerable".

TODO: SetFit upgrade when 150 labels/class accumulated from logs.
"""

from __future__ import annotations

import logging
import re

import numpy as np

from src.retrieval.embed_memory import embed_text, embed_texts

logger = logging.getLogger("ember.intent_classifier")


# ---------------------------------------------------------------------------
# Stage 1: Structural rules with compound first-person guard
# ---------------------------------------------------------------------------

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
            # is present AND there's no external-world anchor. Without the
            # guard, "I'm currently watching the news" would route to internet
            # because of the news signal, even though it's a personal statement.
            if has_first_person and not has_external_anchor:
                return "vault_answerable"
            return "needs_internet"
    return None


# ---------------------------------------------------------------------------
# Stage 2: Embedding similarity against the labeled example pool
# ---------------------------------------------------------------------------
# Reuses the existing Ollama nomic-embed-text pipeline so no new dependency
# is introduced. Example embeddings are lazy-loaded once per process and
# cached in memory.

_STAGE2_CONFIDENCE_THRESHOLD: float = 0.65

_example_embeddings: list[tuple[str, list[float]]] | None = None


def _get_example_embeddings() -> list[tuple[str, list[float]]] | None:
    """Lazy-load and cache (label, embedding) pairs for the example pool.

    Returns None on any failure so Stage 2 can escalate gracefully without
    raising. The most common failure mode is Ollama being unreachable.
    """
    global _example_embeddings
    if _example_embeddings is not None:
        return _example_embeddings
    try:
        from src.llm.classifier_examples import EXAMPLES

        texts = [ex["query"] for ex in EXAMPLES]
        labels = [ex["label"] for ex in EXAMPLES]
        embeddings = embed_texts(texts)
        _example_embeddings = list(zip(labels, embeddings))
        return _example_embeddings
    except Exception as exc:
        logger.warning(
            "[INTENT_CLASSIFY] Stage 2 example embedding load failed (non-fatal): %s",
            exc,
        )
        return None


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two vectors. Returns 0.0 on degenerate input."""
    a_arr = np.asarray(a, dtype=np.float32)
    b_arr = np.asarray(b, dtype=np.float32)
    denom = float(np.linalg.norm(a_arr) * np.linalg.norm(b_arr))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / denom)


def _stage2_classify(query: str) -> tuple[str | None, float | None]:
    """Stage 2: embedding similarity against labeled examples.

    Returns (label, confidence) when the top-1 similarity is at or above
    the threshold; otherwise (None, top_confidence_or_none) so the caller
    can escalate.
    """
    if not query:
        return None, None

    examples = _get_example_embeddings()
    if not examples:
        return None, None

    try:
        query_emb = embed_text(query)
    except Exception as exc:
        logger.warning(
            "[INTENT_CLASSIFY] Stage 2 query embed failed (non-fatal): %s",
            exc,
        )
        return None, None

    scored = [(label, _cosine_similarity(query_emb, emb)) for label, emb in examples]
    scored.sort(key=lambda item: item[1], reverse=True)
    top_label, top_conf = scored[0]

    if top_conf >= _STAGE2_CONFIDENCE_THRESHOLD:
        return top_label, top_conf
    return None, top_conf


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Safe default applied at every escalation path. Matches the ADR-034 Stage 3
# timeout fallback: when uncertain, treat as vault-answerable and let the
# user explicitly request a search if they want one.
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

    stage2_label, stage2_conf = _stage2_classify(query)
    if stage2_label is not None:
        _log(stage="stage2", label=stage2_label, confidence=stage2_conf, query=query)
        return stage2_label

    # Stage 3 not yet implemented on this branch — escalate to safe default.
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
