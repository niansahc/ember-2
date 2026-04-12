"""
src/context/lodestone_resolver.py

Retrieves the most relevant lodestone living layer records for a query.

Returns 0-2 confirmed lodestone records ranked by semantic similarity
to the current user message. Token budget: 100 tokens total for
living layer injection.

See ADR-017, TDD §48.
"""

from __future__ import annotations

import logging
import math

from src.memory.lodestone_service import read_active
from src.retrieval.embedding_model import embed_text

logger = logging.getLogger("ember.lodestone_resolver")

TOKEN_BUDGET = 100


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _token_estimate(text: str) -> int:
    return int(len(text.split()) * 1.3)


def resolve(
    user_message: str,
    max_records: int = 2,
    query_embedding: list[float] | None = None,
) -> list[dict]:
    """
    Return the most relevant confirmed lodestone records for the query.

    Uses semantic similarity between the user message and lodestone values.
    Returns up to max_records records, capped at TOKEN_BUDGET tokens total.
    Returns empty list if no confirmed records exist or embedding fails.

    If query_embedding is provided, reuses it instead of computing a new one.
    """
    active = read_active()
    if not active:
        return []

    if query_embedding is None:
        try:
            query_embedding = embed_text(user_message)
        except Exception as exc:
            logger.warning("[LODESTONE_RESOLVER] Embedding failed: %s", exc)
            return []

    scored = []
    for record in active:
        value_text = record.get("value", "")
        if not value_text:
            continue
        try:
            value_embedding = embed_text(value_text)
            sim = _cosine_similarity(query_embedding, value_embedding)
            scored.append((sim, record))
        except Exception:
            continue

    scored.sort(key=lambda x: x[0], reverse=True)

    selected = []
    token_count = 0
    for sim, record in scored[:max_records]:
        value_text = record.get("value", "")
        est = _token_estimate(value_text)
        if token_count + est > TOKEN_BUDGET:
            break
        selected.append(record)
        token_count += est

    return selected


def to_prompt_text(records: list[dict]) -> str:
    """Render lodestone living layer records for context packet injection."""
    if not records:
        return ""
    lines = []
    for rec in records:
        lines.append(f"- {rec.get('value', '')}")
    return "<lodestone_living>\n" + "\n".join(lines) + "\n</lodestone_living>"
