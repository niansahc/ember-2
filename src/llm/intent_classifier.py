"""
src/llm/intent_classifier.py

Three-tier intent cascade for ask-first routing (ADR-034).

Classifies each user query as either:
  - needs_internet   : query requires current/external information
  - vault_answerable : query should be answered from the user's personal vault

Architecture (ADR-034):
  Stage 1  Structural rules with compound first-person guard   ~2ms
  Stage 2  Embedding similarity against labeled examples       ~30-50ms
  Stage 3  qwen3:8b non-thinking with JSON grammar, 1500ms cap ~300-1500ms

Stage 3 runs qwen3:8b in a background thread with a hard timeout read
from INTENT_CLASSIFIER_TIMEOUT_MS (default 1500ms). On timeout, the
classifier returns the safe default (vault_answerable) per the ADR-034
behavioral contract.

TODO: SetFit upgrade when 150 labels/class accumulated from logs.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re

import numpy as np
import ollama

from src.core.config import (
    get_ember_classifier_telemetry,
    get_ember_debug,
    get_ember_model,
    get_intent_classifier_timeout_ms,
)
from src.retrieval.embed_memory import embed_text, embed_texts

logger = logging.getLogger("ember.intent_classifier")


# ---------------------------------------------------------------------------
# Labels (ADR-034)
# ---------------------------------------------------------------------------

NEEDS_INTERNET: str = "needs_internet"
VAULT_ANSWERABLE: str = "vault_answerable"
_VALID_LABELS: frozenset[str] = frozenset({NEEDS_INTERNET, VAULT_ANSWERABLE})

# Safe default applied at every escalation path. Matches the ADR-034 Stage 3
# timeout fallback: when uncertain, treat as vault-answerable and let the
# user explicitly request a search if they want one.
_SAFE_DEFAULT: str = VAULT_ANSWERABLE


# ---------------------------------------------------------------------------
# Stage 1: Structural rules with compound first-person guard
# ---------------------------------------------------------------------------

# Bare conversational acknowledgments that must never trigger web search.
# Matched against the ENTIRE normalized message — substring matching would
# false-positive on phrases like "thanks for the news" (which legitimately
# might warrant a search). The Stage 3 LLM previously misclassified these
# as needs_internet because their bare form provides no vault context for
# the model to anchor on; this short-circuit keeps them out of the cascade.
_DEFINITE_VAULT_ANSWERABLE_PHRASES: frozenset[str] = frozenset({
    "thank you",
    "thanks",
    "thank u",
    "okay",
    "ok",
    "k",
    "got it",
    "you're welcome",
    "youre welcome",
    "you're right",
    "youre right",
    "i appreciate it",
    "appreciate it",
    "no worries",
    "fair enough",
    "noted",
    "sounds good",
    "makes sense",
    "understood",
    "cool",
    "nice",
})

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

    Returns NEEDS_INTERNET or VAULT_ANSWERABLE when confident; None when
    the query should escalate to Stage 2.
    """
    if not query:
        return None

    # Bare conversational acknowledgments short-circuit straight to
    # vault_answerable. Match the entire normalized message so phrases that
    # merely contain an ack-word as a prefix ("thanks for the news") still
    # flow through to the rest of the cascade.
    normalized = (
        query.lower()
        .replace("‘", "'")
        .replace("’", "'")
        .strip()
        .rstrip(".!?,")
        .strip()
    )
    if normalized in _DEFINITE_VAULT_ANSWERABLE_PHRASES:
        return VAULT_ANSWERABLE

    has_first_person = bool(FIRST_PERSON_MARKERS.search(query))
    has_external_anchor = bool(EXTERNAL_WORLD_ANCHORS.search(query))

    for signal in DEFINITE_INTERNET_SIGNALS:
        if signal.search(query):
            # Compound guard: block the internet signal only when first-person
            # is present AND there's no external-world anchor. Without the
            # guard, "I'm currently watching the news" would route to internet
            # because of the news signal, even though it's a personal statement.
            if has_first_person and not has_external_anchor:
                return VAULT_ANSWERABLE
            return NEEDS_INTERNET
    return None


# ---------------------------------------------------------------------------
# Stage 2: Embedding similarity against the labeled example pool
# ---------------------------------------------------------------------------
# Reuses the existing Ollama nomic-embed-text pipeline so no new dependency
# is introduced. Example embeddings are lazy-loaded once per process and
# cached as a pre-normalized numpy matrix so each query only costs one
# embed call + one matrix-vector dot product.

_STAGE2_CONFIDENCE_THRESHOLD: float = 0.65

# Cache shape: (labels_list, normalized_matrix) where matrix rows are unit
# vectors so cosine similarity reduces to a pure dot product at query time.
_example_embeddings: tuple[list[str], np.ndarray] | None = None


def _get_example_embeddings() -> tuple[list[str], np.ndarray] | None:
    """Lazy-load and cache the (labels, normalized-matrix) pair.

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
        raw_embeddings = embed_texts(texts)
        matrix = np.asarray(raw_embeddings, dtype=np.float32)
        # Unit-normalize each row so cosine similarity becomes matrix @ q.
        # Guard against zero-norm rows (shouldn't happen with real embeddings
        # but defensive — np.where avoids the divide-by-zero warning).
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms = np.where(norms == 0.0, 1.0, norms)
        matrix = matrix / norms
        _example_embeddings = (labels, matrix)
        return _example_embeddings
    except Exception as exc:
        if get_ember_debug():
            # Exception payload may include text from the example pool; gate
            # behind EMBER_DEBUG so it does not enter stdout by default.
            logger.warning(
                "[INTENT_CLASSIFY] Stage 2 example embedding load failed (non-fatal): %s",
                exc,
            )
        return None


def _stage2_classify(query: str) -> tuple[str | None, float | None]:
    """Stage 2: embedding similarity against labeled examples.

    Returns (label, confidence) when the top-1 similarity is at or above
    the threshold; otherwise (None, top_confidence_or_none) so the caller
    can escalate.
    """
    if not query:
        return None, None

    cached = _get_example_embeddings()
    if cached is None:
        return None, None
    labels, matrix = cached

    try:
        query_emb = embed_text(query)
    except Exception as exc:
        if get_ember_debug():
            # Exception payload may echo the query; gate behind EMBER_DEBUG.
            logger.warning(
                "[INTENT_CLASSIFY] Stage 2 query embed failed (non-fatal): %s",
                exc,
            )
        return None, None

    query_vec = np.asarray(query_emb, dtype=np.float32)
    query_norm = float(np.linalg.norm(query_vec))
    if query_norm == 0.0:
        return None, None
    query_vec = query_vec / query_norm

    # Matrix rows and query_vec are both unit-normalized → cosine is the dot.
    scores = matrix @ query_vec
    top_idx = int(np.argmax(scores))
    top_conf = float(scores[top_idx])
    top_label = labels[top_idx]

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
#
# The executor is per-call rather than a module singleton: with max_workers=1,
# a stuck Ollama call would cascade-timeout every subsequent Stage 3 query
# because later submissions would queue behind it. Per-call executors let
# concurrent classify_intent calls attempt Stage 3 in parallel; the creation
# overhead (~2-5ms) is noise next to the 1500ms budget.

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
        if label in _VALID_LABELS:
            return label
        if get_ember_debug():
            logger.warning(
                "[INTENT_CLASSIFY] Stage 3 returned unknown label: %r", label
            )
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        if get_ember_debug():
            # Exception may include raw LLM output that echoes the query.
            logger.warning("[INTENT_CLASSIFY] Stage 3 JSON parse failed: %s", exc)
    except Exception as exc:
        if get_ember_debug():
            # Exception may include the request payload sent to Ollama.
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
            return future.result(timeout=timeout_s), False
        except concurrent.futures.TimeoutError:
            return _SAFE_DEFAULT, True
    finally:
        executor.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_intent(query: str) -> str:
    """Classify a user query as NEEDS_INTERNET or VAULT_ANSWERABLE.

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


_PROPER_NOUN_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")
_DIGIT_RUN_RE = re.compile(r"\b\d{4,}\b")
_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")


def _scrub_for_telemetry(query: str) -> str:
    """Strip PII-like tokens from a query before logging to telemetry.

    Preserves intent-discriminative structure (interrogative words, verbs,
    possessive markers) while replacing multi-word Title Case sequences
    with [PROPER], 4+ digit runs with [NUM], and email addresses with
    [EMAIL]. Output is ASCII-only and truncated to 60 chars.

    Single-word proper nouns and the first capitalized word of a sentence
    are not stripped; the latter would over-fire on every query that
    starts with "What", "Where", etc. The privacy/utility tradeoff is
    noted in classifier_examples.py.
    """
    if not query:
        return ""
    scrubbed = _EMAIL_RE.sub("[EMAIL]", query)
    scrubbed = _PROPER_NOUN_RE.sub("[PROPER]", scrubbed)
    scrubbed = _DIGIT_RUN_RE.sub("[NUM]", scrubbed)
    scrubbed = scrubbed.encode("ascii", "ignore").decode("ascii")
    return scrubbed[:60]


def _log(stage: str, label: str, confidence: float | None, query: str) -> None:
    """Emit the structured classification log line.

    Gated behind EMBER_CLASSIFIER_TELEMETRY (separate from EMBER_DEBUG)
    so the ADR-034 training-data pipeline can run independently of full
    diagnostic logging. Returns silently when telemetry is unset.

    Query is scrubbed via _scrub_for_telemetry before logging — proper
    nouns, digit runs, and emails are replaced with placeholders. The
    raw query never enters stdout from this call.

    ASCII-only to avoid Windows cp1252 corruption (CLAUDE.md rule 7).
    """
    if not get_ember_classifier_telemetry():
        return
    logger.info(
        "[INTENT_CLASSIFY] stage=%s label=%s confidence=%s query=%s",
        stage,
        label,
        "none" if confidence is None else f"{confidence:.3f}",
        _scrub_for_telemetry(query),
    )
