"""
src/safety/pattern_detector.py

ADR-021 cross-session pattern detection.

Operates over already-retrieved conversation records (no extra vault
reads). Embeddings are read from cached metadata populated at write
time (per ADR-021 amendment 2026-04-24).

PatternSignal lives in src.safety.models alongside the other safety
dataclasses (re-exported below for back-compat with imports of the
detector module).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from src.safety.models import PatternSignal, T2_CATEGORIES  # noqa: F401 (re-export)

if TYPE_CHECKING:
    from src.context.models import ContextItem


logger = logging.getLogger("ember.pattern_detector")


# Hyperparameters per ADR-021. Amendment 2026-04-24: the recency gate
# is a tunable hyperparameter, not theory-derived.
T2_MIN_INSTANCES = 3
T2_MIN_SIMILARITY = 0.82
T2_MIN_SESSIONS = 2
T2_RECENCY_DAYS = 30

# Match write_memory._next_timestamp format so cutoff and record
# timestamps are string-comparable. Both are zero-padded fixed-width,
# so lexical compare is correct.
_TIMESTAMP_FMT = "%Y-%m-%dT%H-%M-%S-%f"


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def detect_t2_pattern(
    retrieved_items: "list[ContextItem]",
    query_embedding: list[float] | None,
) -> PatternSignal | None:
    """Detect a T2 cross-session relational pattern. Returns None if any
    threshold per ADR-021 is not met.

    TODO (deferred per plan Q4): post-generation detection of whether
    Ember named the pattern, and writing a system_event audit record.
    """
    if not query_embedding:
        return None

    convs = [item for item in retrieved_items if item.memory_type == "conversation"]
    if len(convs) < T2_MIN_INSTANCES:
        return None

    candidates: list["ContextItem"] = []
    max_sim = 0.0
    cache_misses = 0
    for item in convs:
        emb = item.metadata.get("embedding")
        if not emb:
            cache_misses += 1
            continue
        sim = _cosine_similarity(query_embedding, emb)
        if sim >= T2_MIN_SIMILARITY:
            candidates.append(item)
            if sim > max_sim:
                max_sim = sim

    if cache_misses:
        logger.info(
            "[T2_DETECTOR] cache_miss skipped %d/%d conversation records "
            "(legacy records lacking cached embedding)",
            cache_misses,
            len(convs),
        )

    if len(candidates) < T2_MIN_INSTANCES:
        return None

    session_ids = {item.metadata.get("session_id") or "" for item in candidates}
    session_ids.discard("")
    if len(session_ids) < T2_MIN_SESSIONS:
        return None

    cutoff = (datetime.now() - timedelta(days=T2_RECENCY_DAYS)).strftime(_TIMESTAMP_FMT)
    if not any((item.timestamp or "") >= cutoff for item in candidates):
        return None

    has_named_party = any(
        bool(item.metadata.get("contains_named_third_party", False))
        for item in candidates
    )

    signal = PatternSignal(
        instance_count=len(candidates),
        session_count=len(session_ids),
        has_named_party=has_named_party,
        max_similarity=max_sim,
        category="relational",
    )

    logger.info(
        "[T2_AUDIT] pattern_signal_emitted instance_count=%d "
        "session_count=%d has_named_party=%s max_similarity=%.3f",
        signal.instance_count,
        signal.session_count,
        signal.has_named_party,
        signal.max_similarity,
    )

    return signal
