"""
src/reflection/lodestone_synthesis.py

Path 2 lodestone acquisition (ADR-017): three-stage reflection synthesis
that proposes inferred value records when a recurring theme is visible
across the prior month's reflections.

Most runs short-circuit at Stage 1 or 2. That is correct behavior per
ADR-017 - the architecture trades yield for signal quality.

Output records are written with confirmed=False; the user confirms via
PATCH /v1/lodestone/{id}. Until confirmed, lodestone_resolver does not
inject them into the prompt (read_active() filters confirmed-only).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import ollama

from src.core.config import get_ember_model
from src.memory import lodestone_service
from src.memory.service import MemoryService

logger = logging.getLogger("ember.lodestone_synthesis")


# Hyperparameters per ADR-017. Tunable - revisit after real usage data exists.
SYNTHESIS_WINDOW_DAYS = 30
MIN_REFLECTIONS_FOR_SYNTHESIS = 4
MAX_REFLECTIONS_INPUT = 30
REFLECTION_TRUNCATE_CHARS = 400
STAGE1_MIN_THEME_WORDS = 5  # density floor: rejects single-token themes ("honesty")

VALID_CATEGORIES = {"character", "relational", "directional", "ground", "beyond"}


_STAGE1_PROMPT = """\
You are reviewing one month of reflection summaries from a personal AI's
journal of a user's behavior and stated thinking. Your task is to identify
whether ONE specific theme recurs across these reflections.

A theme qualifies only if:
- It appears in 3 or more separate reflections (not just 3 mentions in one)
- It is something the user keeps returning to of their own volition, not
  a topic the AI introduced
- It is concrete enough that a stranger could describe what behavior would
  follow from it

Output:
- If a qualifying theme is present, write the theme in ONE short phrase
  (under 12 words). Example: "the user keeps returning to honest
  conversation over comfortable conversation"
- If no qualifying theme is present, output exactly: NO_VALUE_FOUND

Do not list multiple themes. Do not output JSON. Do not explain. Output
the phrase OR the literal string NO_VALUE_FOUND.

Reflections (one per record, separated by ---):

{reflection_block}
"""


_STAGE2_PROMPT = """\
You are deciding whether a recurring theme expresses a VALUE the user
holds, or whether it is a SITUATION the user is currently navigating.

A value is something the user is committed to over time, that would
influence behavior across many situations. A situation is a current
project, problem, or context that will pass.

Theme:
"{theme}"

If this is a value, name the single best taxonomy category for it:
- character: what kind of person am I committed to being?
- relational: how do I hold my responsibilities to people I'm connected to?
- directional: what am I moving toward or guarding?
- ground: what do I draw from when everything else is uncertain?
- beyond: what connects me to something larger than myself?

Output exactly ONE of these strings:
- character
- relational
- directional
- ground
- beyond
- NO_CATEGORY_MATCH

Output the literal string only. No explanation.
"""


_STAGE3_PROMPT = """\
You are writing a candidate lodestone record for a user. The theme
recurred across this month and was classified as a {category} value.

Write a single, specific value statement in the user's voice (first person,
present tense, declarative). Avoid abstractions ("integrity"); write the
concrete commitment ("I'd rather lose comfort than skip a hard conversation
that needs to happen"). Keep it under 25 words.

Then list the supporting evidence: 2-4 short verbatim or paraphrased
excerpts from the reflections that demonstrate the pattern. Each excerpt
on its own line, prefixed with "- ".

Theme: "{theme}"
Category: {category}

Reflections:
{reflection_block}

Output exactly this format:
VALUE: <one sentence value statement>
EVIDENCE:
- <excerpt 1>
- <excerpt 2>
[- <excerpt 3>]
[- <excerpt 4>]
"""


def _format_reflection_block(reflections: list[dict]) -> str:
    """Build the reflection input block. Capped at MAX_REFLECTIONS_INPUT records,
    each truncated to REFLECTION_TRUNCATE_CHARS characters."""
    lines: list[str] = []
    for rec in reflections[:MAX_REFLECTIONS_INPUT]:
        ts = rec.get("timestamp") or rec.get("created_at") or ""
        date_prefix = ts[:10] if ts else "unknown"
        text = (rec.get("text") or rec.get("summary") or "").strip()
        if not text:
            continue
        truncated = text[:REFLECTION_TRUNCATE_CHARS].rstrip()
        if len(text) > REFLECTION_TRUNCATE_CHARS:
            truncated += "..."
        lines.append(f"[{date_prefix}] {truncated}")
    return "\n---\n".join(lines)


def _ollama_text(prompt: str, num_predict: int) -> str | None:
    """Single-message Ollama call with deterministic settings. Returns None on
    error so callers short-circuit cleanly."""
    try:
        result = ollama.chat(
            model=get_ember_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": num_predict},
            think=False,
        )
        return (result.get("message") or {}).get("content", "").strip()
    except Exception as exc:
        logger.warning("[LODESTONE_SYNTHESIS] LLM call failed: %s", exc)
        return None


def _stage1_pattern_check(reflection_block: str) -> str | None:
    """Return the theme phrase, or None if NO_VALUE_FOUND / too abstract / error."""
    response = _ollama_text(_STAGE1_PROMPT.format(reflection_block=reflection_block), num_predict=30)
    if not response:
        return None
    if response.upper().strip() == "NO_VALUE_FOUND":
        logger.info("[LODESTONE_SYNTHESIS] stage1 NO_VALUE_FOUND - exiting")
        return None
    if len(response.split()) < STAGE1_MIN_THEME_WORDS:
        logger.info(
            "[LODESTONE_SYNTHESIS] stage1 theme too abstract (<%d words): %r - exiting",
            STAGE1_MIN_THEME_WORDS,
            response[:80],
        )
        return None
    return response


def _stage2_taxonomy_check(theme: str) -> str | None:
    """Return the category in VALID_CATEGORIES, or None for any other response."""
    response = _ollama_text(_STAGE2_PROMPT.format(theme=theme), num_predict=10)
    if not response:
        return None
    normalized = response.strip().lower().split()[0] if response.strip() else ""
    if normalized in VALID_CATEGORIES:
        return normalized
    logger.info(
        "[LODESTONE_SYNTHESIS] stage2 no category match (response=%r) - exiting",
        response[:60],
    )
    return None


def _parse_stage3_output(text: str) -> tuple[str, list[str]] | None:
    """Parse VALUE: + EVIDENCE: lines. Returns (value, [evidence_lines]) or None
    on parse failure / missing markers / empty value / zero evidence."""
    lines = text.splitlines()
    value = ""
    evidence: list[str] = []
    in_evidence = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith("VALUE:"):
            value = stripped.split(":", 1)[1].strip()
            in_evidence = False
        elif upper.startswith("EVIDENCE:"):
            in_evidence = True
        elif in_evidence and stripped.startswith("-"):
            excerpt = stripped.lstrip("-").strip()
            if excerpt:
                evidence.append(excerpt)
    if not value or not evidence:
        return None
    return value, evidence


def _stage3_record_draft(
    theme: str, category: str, reflection_block: str
) -> tuple[str, list[str]] | None:
    """Return parsed (value, evidence) or None on parser/LLM failure."""
    response = _ollama_text(
        _STAGE3_PROMPT.format(theme=theme, category=category, reflection_block=reflection_block),
        num_predict=200,
    )
    if not response:
        return None
    parsed = _parse_stage3_output(response)
    if parsed is None:
        logger.info("[LODESTONE_SYNTHESIS] stage3 parser failure - exiting")
        return None
    return parsed


def _recent_reflections(memory_service: MemoryService) -> list[dict]:
    """Return reflection records timestamped within the last
    SYNTHESIS_WINDOW_DAYS, sorted ascending by timestamp."""
    records = memory_service.read(memory_type="reflection", limit=MAX_REFLECTIONS_INPUT * 2)
    cutoff = (datetime.now() - timedelta(days=SYNTHESIS_WINDOW_DAYS)).strftime(
        "%Y-%m-%dT%H-%M-%S-%f"
    )
    in_window = [
        rec for rec in records
        if (rec.get("timestamp") or rec.get("created_at") or "") >= cutoff
    ]
    in_window.sort(key=lambda r: r.get("timestamp") or r.get("created_at") or "")
    return in_window


def synthesize_lodestone_candidates(
    memory_service: MemoryService | None = None,
) -> dict | None:
    """Run the three-stage path-2 synthesis. Writes a proposed lodestone
    record on full success; returns the written record dict or None.

    The function is intentionally side-effecting (writes a vault record)
    rather than returning a draft for the caller to commit - matches the
    pattern of generate_reflection() and keeps the runner glue trivial.
    """
    svc = memory_service or MemoryService()
    reflections = _recent_reflections(svc)
    if len(reflections) < MIN_REFLECTIONS_FOR_SYNTHESIS:
        logger.info(
            "[LODESTONE_SYNTHESIS] insufficient evidence: %d reflections in window",
            len(reflections),
        )
        return None

    reflection_block = _format_reflection_block(reflections)
    if not reflection_block:
        logger.info("[LODESTONE_SYNTHESIS] reflection block empty after formatting")
        return None

    theme = _stage1_pattern_check(reflection_block)
    if theme is None:
        return None

    category = _stage2_taxonomy_check(theme)
    if category is None:
        return None

    parsed = _stage3_record_draft(theme, category, reflection_block)
    if parsed is None:
        return None

    value, evidence = parsed
    # TODO (v0.18.0+): set recurrence_count from len(evidence) once the UI
    # surfaces "this came up N times this month" on the confirmation queue.
    # MVP keeps the lodestone_service default of 1.
    record = lodestone_service.write(
        value=value,
        taxonomy_category=category,
        acquisition_path="inferred",
        source="reflection_synthesis",
        supporting_evidence="\n".join(f"- {line}" for line in evidence),
        confirmed=False,
    )
    logger.info(
        "[LODESTONE_SYNTHESIS] proposed inferred record (%s): %s",
        category,
        value[:80],
    )
    return record
