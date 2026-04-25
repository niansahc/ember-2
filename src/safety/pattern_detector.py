"""
src/safety/pattern_detector.py

ADR-021 cross-session pattern detection signal.

Detects when retrieved conversation records form a recurring relational
pattern across multiple sessions, surfacing a structured signal that
gets injected into the prompt (as <cross_session_pattern>) and into
the constitutional review context (per ADR-035 / Item 7).

The detector operates over already-retrieved conversation records — no
additional vault reads. Embeddings are read from cached metadata
populated at write time (per ADR-021 amendment 2026-04-24).

Carries only structural metadata (counts, max similarity, category,
boolean named-third-party flag). Never contains record content, ids,
or third-party identifiers.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.context.models import ContextItem


logger = logging.getLogger("ember.pattern_detector")


# Hyperparameters per ADR-021 §"Evidence threshold".
# Amendment 2026-04-24: the 30-day recency gate is a tunable
# hyperparameter, not theory-derived. Adjust empirically as needed.
T2_MIN_INSTANCES = 3
T2_MIN_SIMILARITY = 0.82
T2_MIN_SESSIONS = 2
T2_RECENCY_DAYS = 30


# Category taxonomy. ADR-021 currently scopes to relational_honesty T2,
# so "relational" is the only category in the MVP. The field exists
# because ADR-035 / SafetyReviewContext.t2_pattern_category is a string
# label that may carry richer taxonomies in future ADRs.
T2_CATEGORIES = ("relational",)


@dataclass(frozen=True)
class PatternSignal:
    """Cross-session pattern detection result, per ADR-021.

    Carries only structural metadata - counts and a category label.
    Never contains record content, ids, or third-party identifiers.

    Consumed by:
      - PromptBuilder._build_cross_session_pattern_block (ADR-021 flag)
      - SafetyReviewContext.t2_pattern_category (ADR-035 review hook,
        via context_packet.t2_pattern_signal.category)
    """

    instance_count: int        # number of similar conversation records
    session_count: int         # distinct session_ids spanned
    has_named_party: bool      # any candidate has contains_named_third_party=True
    max_similarity: float      # max cosine similarity in the cluster
    category: str = "relational"  # taxonomy label (MVP: only "relational")


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Module-local cosine helper. Mirrors src/context/lodestone_resolver.py
    so this module has no cross-package dependency on private symbols."""
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

    Inputs are the already-retrieved context items and the pre-computed
    query embedding (both available on ContextPacket post-retrieval).

    The detector:
      1. Filters to memory_type == "conversation" only (per ADR-021 -
         state, reflection, lodestone are derived/operational artifacts
         that would contaminate the signal).
      2. Computes cosine similarity between query and each candidate's
         cached embedding (read from metadata, populated at write time
         per ADR-021 amendment).
      3. Applies four thresholds: instance count, similarity floor,
         distinct session count, recency gate.
      4. If all pass, derives `has_named_party` from any candidate's
         `contains_named_third_party` metadata flag.

    Returns None on:
      - Missing query_embedding
      - Fewer than T2_MIN_INSTANCES retrieved conversations
      - Fewer than T2_MIN_INSTANCES candidates above the similarity floor
      - Fewer than T2_MIN_SESSIONS distinct session_ids in the cluster
      - No candidates within T2_RECENCY_DAYS

    TODO (Item 8 follow-up, deferred per Q4): post-generation detection
    of whether Ember named the pattern, and writing a system_event audit
    record. ADR-021 specifies this for human review; phrase-detection
    heuristic needs design before implementation.
    """
    if not query_embedding:
        return None

    convs = [
        item for item in retrieved_items
        if getattr(item, "memory_type", None) == "conversation"
    ]
    if len(convs) < T2_MIN_INSTANCES:
        return None

    candidates: list[tuple["ContextItem", float]] = []
    cache_misses = 0
    for item in convs:
        emb = (item.metadata or {}).get("embedding")
        if not emb:
            # Cache miss: skip rather than recompute. Per ADR-021 amendment,
            # writes are expected to populate the cache. Missing means a
            # legacy record predating the amendment - candidate for backfill.
            cache_misses += 1
            continue
        sim = _cosine_similarity(query_embedding, emb)
        if sim >= T2_MIN_SIMILARITY:
            candidates.append((item, sim))

    if cache_misses:
        # Single log line per turn (not per record) to surface coverage gaps
        # without flooding logs. Backfill script (deferred per plan Q5) would
        # close this over time.
        logger.info(
            "[T2_DETECTOR] cache_miss skipped %d/%d conversation records "
            "(legacy records lacking cached embedding)",
            cache_misses,
            len(convs),
        )

    if len(candidates) < T2_MIN_INSTANCES:
        return None

    session_ids = {
        ((item.metadata or {}).get("session_id") or "")
        for item, _ in candidates
    }
    session_ids.discard("")  # records missing session_id don't count
    if len(session_ids) < T2_MIN_SESSIONS:
        return None

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=T2_RECENCY_DAYS)
    ).isoformat()
    if not any(
        (item.timestamp or "") >= cutoff for item, _ in candidates
    ):
        return None

    has_named_party = any(
        bool((item.metadata or {}).get("contains_named_third_party", False))
        for item, _ in candidates
    )

    signal = PatternSignal(
        instance_count=len(candidates),
        session_count=len(session_ids),
        has_named_party=has_named_party,
        max_similarity=max(sim for _, sim in candidates),
        category="relational",
    )

    # Audit log hook (Q4 deferred decision: logging only for MVP, no
    # vault system_event write). Surfaces detector firings without the
    # complexity of post-generation phrase detection.
    logger.info(
        "[T2_AUDIT] pattern_signal_emitted instance_count=%d "
        "session_count=%d has_named_party=%s max_similarity=%.3f",
        signal.instance_count,
        signal.session_count,
        signal.has_named_party,
        signal.max_similarity,
    )

    return signal
