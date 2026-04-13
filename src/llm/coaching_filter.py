"""
src/llm/coaching_filter.py

Two-stage post-generation filter for coaching-frame patterns.

Stage 1: Pattern matcher — detects and removes coaching-frame artifacts
from emotional/relational responses. Fast, no LLM call.

Stage 2: Rewrite call — fires only when Stage 1 detects a pattern that
requires natural language rewriting (deletion alone would leave an
incomplete response). Uses the smallest available Ollama model.

Only fires on emotional and relational intent classes. Factual and
analytical queries pass through untouched.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("ember.coaching_filter")

# Intent classes that trigger the filter.
_EMOTIONAL_INTENTS = frozenset({"reflective", "default"})


# ---------------------------------------------------------------------------
# Stage 1: Pattern definitions
# ---------------------------------------------------------------------------

# Coaching-frame closing patterns — these end responses with guided
# self-discovery or action-prompting language.
_COACHING_CLOSINGS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"what(?:'s| is| would be) the first step",
    r"i(?:'ll| will) help you (?:map|work|figure|sort|think) (?:it |that |this )?out",
    r"let me know (?:what you need|if you need|how i can|what i can)",
    r"what would you like to (?:do|try|start with|focus on)",
    r"let(?:'s| us) (?:tackle|work on|start with|break (?:it|this|that) down)",
    r"what(?:'s| is) (?:holding you back|stopping you|in your way)",
    r"i(?:'m| am) here (?:if|when|whenever) you (?:want|need|are ready)",
))

# Therapeutic openers — validate/normalize feelings in a clinical way.
_THERAPEUTIC_OPENERS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"^i(?:'m| am) here (?:if|for) you",
    r"^it(?:'s| is) (?:okay|ok|perfectly (?:okay|ok|fine|normal)) to feel",
    r"^(?:that|your) (?:feeling|emotion|reaction) is (?:valid|completely valid|understandable|normal)",
    r"^(?:i hear you|i see you|i understand)",
))

# Sycophantic openers under pushback — agreement-seeking as first word(s).
_SYCOPHANTIC_OPENERS: tuple[re.Pattern, ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"^you(?:'re| are) right",
    r"^(?:sure|absolutely|of course|fair (?:enough|point))[.,!]?\s",
    r"^(?:yes|yeah)[.,!]?\s+(?:i|you|that|if)",
))

# Numbered structure patterns on emotional content.
_NUMBERED_STRUCTURES: tuple[re.Pattern, ...] = (
    re.compile(r"(?:^|\n)\s*(?:1\.|first,)\s+", re.IGNORECASE | re.MULTILINE),
)


# ---------------------------------------------------------------------------
# Stage 1: Detection
# ---------------------------------------------------------------------------

def _detect_patterns(text: str, is_emotional: bool) -> list[dict]:
    """Detect coaching-frame patterns in the response text.

    Returns a list of match dicts: {"pattern": str, "match": str, "position": str, "deletable": bool}
    """
    if not is_emotional:
        return []

    matches: list[dict] = []

    # Coaching closings — check the last 200 chars
    tail = text[-200:] if len(text) > 200 else text
    for pat in _COACHING_CLOSINGS:
        m = pat.search(tail)
        if m:
            matches.append({
                "pattern": "coaching_closing",
                "match": m.group(),
                "position": "tail",
                "deletable": True,
            })

    # Therapeutic openers — check the first 100 chars
    head = text[:100]
    for pat in _THERAPEUTIC_OPENERS:
        m = pat.search(head)
        if m:
            matches.append({
                "pattern": "therapeutic_opener",
                "match": m.group(),
                "position": "head",
                "deletable": False,  # Needs rewrite, not just deletion
            })

    # Sycophantic openers — check the first 50 chars
    head_short = text[:50]
    for pat in _SYCOPHANTIC_OPENERS:
        m = pat.search(head_short)
        if m:
            matches.append({
                "pattern": "sycophantic_opener",
                "match": m.group(),
                "position": "head",
                "deletable": False,  # Needs rewrite
            })

    # Numbered structures
    for pat in _NUMBERED_STRUCTURES:
        m = pat.search(text)
        if m:
            matches.append({
                "pattern": "numbered_structure",
                "match": m.group().strip(),
                "position": "body",
                "deletable": False,  # Needs rewrite — structure removal changes meaning
            })

    return matches


# ---------------------------------------------------------------------------
# Stage 1: Deletion (for deletable patterns)
# ---------------------------------------------------------------------------

def _apply_deletions(text: str, matches: list[dict]) -> str:
    """Remove deletable patterns from the response text."""
    result = text
    for m in matches:
        if not m["deletable"]:
            continue

        if m["position"] == "tail":
            # Remove the coaching closing from the end — find the sentence
            # containing the match and strip it.
            pat = re.compile(re.escape(m["match"]), re.IGNORECASE)
            # Find the last sentence containing the match
            sentences = re.split(r'(?<=[.!?])\s+', result)
            cleaned = []
            for s in sentences:
                if not pat.search(s):
                    cleaned.append(s)
            result = " ".join(cleaned)

    return result.strip()


# ---------------------------------------------------------------------------
# Stage 2: Rewrite call
# ---------------------------------------------------------------------------

def _needs_rewrite(matches: list[dict]) -> bool:
    """Return True if any match requires a rewrite (not just deletion)."""
    return any(not m["deletable"] for m in matches)


def _rewrite(text: str, matches: list[dict]) -> str:
    """Call the smallest available Ollama model to rewrite the response.

    Only fires when Stage 1 detected non-deletable patterns.
    """
    try:
        import ollama
        from src.core.config import get_ember_model

        pattern_descriptions = "; ".join(
            f"{m['pattern']}: \"{m['match']}\"" for m in matches if not m["deletable"]
        )

        prompt = (
            "Rewrite this response to remove coaching-frame elements. "
            "Preserve the factual content and emotional presence. "
            "Remove numbered structures, therapeutic framing, coaching closings, "
            "and sycophantic openers. Keep the response direct and warm. "
            "Return ONLY the rewritten text, nothing else.\n\n"
            f"Problems detected: {pattern_descriptions}\n\n"
            f"Original response:\n{text}"
        )

        response = ollama.chat(
            model=get_ember_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.3, "num_predict": 500},
        )
        rewritten = response["message"]["content"].strip()

        # Sanity check: rewrite should not be empty or drastically longer
        if not rewritten or len(rewritten) > len(text) * 2:
            logger.warning("[COACHING_FILTER] Rewrite rejected — empty or too long")
            return text

        return rewritten

    except Exception as exc:
        logger.warning("[COACHING_FILTER] Stage 2 rewrite failed (non-fatal): %s", exc)
        return text


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log_intervention(
    intent_class: str,
    matches: list[dict],
    original: str,
    result: str,
    stage: int,
) -> None:
    """Log filter intervention to the safety logs directory."""
    try:
        log_dir = Path(__file__).resolve().parents[2] / "logs" / "coaching_filter"
        log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        payload = {
            "timestamp": timestamp,
            "intent_class": intent_class,
            "stage": stage,
            "patterns": matches,
            "original_segment": original[:500],
            "rewritten_segment": result[:500] if result != original else None,
            "changed": result != original,
        }

        file_path = log_dir / f"{timestamp}.json"
        file_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("[COACHING_FILTER] Logged intervention: %s patterns, stage %d", len(matches), stage)

    except Exception as exc:
        logger.warning("[COACHING_FILTER] Logging failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def filter_coaching_frame(
    text: str,
    intent_class: str,
    is_conversational: bool,
) -> str:
    """Apply two-stage coaching-frame filter to a response.

    Only fires on emotional/relational intent. Returns the original
    text unchanged for factual and analytical queries.

    Args:
        text: The full response text (post think-block stripping).
        intent_class: The classified intent (e.g. "reflective", "default").
        is_conversational: Whether the query matched conversational markers.

    Returns:
        The filtered response text.
    """
    is_emotional = intent_class in _EMOTIONAL_INTENTS or is_conversational

    # Stage 1: detect
    matches = _detect_patterns(text, is_emotional)
    if not matches:
        return text

    # Stage 1: apply deletions for deletable patterns
    result = _apply_deletions(text, matches)
    stage = 1

    # Stage 2: rewrite if any non-deletable patterns remain
    if _needs_rewrite(matches):
        result = _rewrite(result, matches)
        stage = 2

    # Log every intervention
    _log_intervention(intent_class, matches, text, result, stage)

    return result
