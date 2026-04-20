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

All three stages are active in this commit. Stage 3 runs qwen3:8b in a
background thread with a hard timeout read from INTENT_CLASSIFIER_TIMEOUT_MS
(default 800ms). On timeout, the classifier returns the safe default
("vault_answerable") per ADR-034 behavioral contract.

TODO: SetFit upgrade when 150 labels/class accumulated from logs.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re

import numpy as np
import ollama

from src.core.config import get_ember_model, get_intent_classifier_timeout_ms
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
# Stage 3: qwen3:8b non-thinking JSON-grammar fallback with hard timeout
# ---------------------------------------------------------------------------
# Runs the local LLM in a background thread so the caller can impose a
# hard timeout. On timeout we return the ADR-mandated safe default
# (vault_answerable) rather than wait for the model to finish. A thread
# left behind on timeout will drain naturally; we do not try to kill it,
# but we do return immediately to the caller.

_STAGE3_SYSTEM_PROMPT: str = (
    "You are a binary intent classifier. Decide whether the user's query "
    "requires current information from the internet, or can be answered "
    "from the user's personal vault of memories and notes. Respond with "
    'ONLY JSON in this exact form: {"label": "needs_internet"} or '
    '{"label": "vault_answerable"}. No other text.'
)


def _stage3_llm_call(query: str) -> str:
    """Single LLM call for Stage 3. Returns a label or the safe default.

    Must not raise — all exceptions are caught and converted to the safe
    default so the caller's timeout wrapper sees a clean return.
    """
    try:
        response = ollama.chat(
            model=get_ember_model(),
            messages=[
                {"role": "system", "content": _STAGE3_SYSTEM_PROMPT},
                {"role": "user", "content": query[:500]},
            ],
            format="json",
            options={"think": False},
        )
        content = response["message"]["content"]
        data = json.loads(content)
        label = data.get("label")
        if label in ("needs_internet", "vault_answerable"):
            return label
        logger.warning(
            "[INTENT_CLASSIFY] Stage 3 returned unknown label: %r", label
        )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.warning("[INTENT_CLASSIFY] Stage 3 JSON parse failed: %s", exc)
    except Exception as exc:
        logger.warning("[INTENT_CLASSIFY] Stage 3 LLM call failed: %s", exc)
    return _SAFE_DEFAULT


def _stage3_classify_with_timeout(query: str) -> tuple[str, bool]:
    """Stage 3 with a hard timeout from INTENT_CLASSIFIER_TIMEOUT_MS.

    Returns (label, timed_out). On timeout the label is the safe default.
    """
    timeout_s = get_intent_classifier_timeout_ms() / 1000.0
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(_stage3_llm_call, query)
        try:
            label = future.result(timeout=timeout_s)
            return label, False
        except concurrent.futures.TimeoutError:
            return _SAFE_DEFAULT, True
    finally:
        # Do not wait for the background thread — it may still be running.
        # Python will reclaim it when it naturally completes.
        executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Safe default applied at every escalation path. Matches the ADR-034 Stage 3
# timeout fallback: when uncertain, treat as vault-answerable and let the
# user explicitly request a search if they want one.
_SAFE_DEFAULT: str = "vault_answerable"


def classify_intent(query: str) -> str:
    """Classify a user query as needs_internet or vault_answerable.

    Runs the full ADR-034 cascade. Always returns one of the two valid
    labels — never raises, never returns None. Emits exactly one
    [INTENT_CLASSIFY] log line per call for the training-data pipeline
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

    stage3_label, timed_out = _stage3_classify_with_timeout(query)
    _log(
        stage="timeout" if timed_out else "stage3",
        label=stage3_label,
        confidence=None,
        query=query,
    )
    return stage3_label


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
