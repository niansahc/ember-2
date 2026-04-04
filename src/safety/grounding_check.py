"""
src/safety/grounding_check.py

Post-generation grounding verification layer (ADR-019).

Checks whether a generated response contains specific factual claims
about the user that are not present in the retrieved vault context.
Distinct from constitutional review (behavioral policy) -- this checks
epistemic fidelity (factual grounding).

Triggered by intent class, not universally. See ADR-019 for design.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import httpx

logger = logging.getLogger("ember.grounding_check")

GROUNDING_CHECK_INTENTS = {
    "factual_recall",
    "status_state",
    "reflective",
    "web_search",
}

GROUNDING_CHECK_PROMPT = """RETRIEVED CONTEXT (verified vault records):
{retrieved_context}

GENERATED RESPONSE:
{response}

Does the generated response contain specific factual claims about the user (their name, relationships, work, projects, history, emotional state, or personal circumstances) that are NOT present in the retrieved context above?

Answer YES or NO only.
If YES, identify the unsupported claims in one sentence."""

REVISION_PROMPT = """The following response contains claims not supported by retrieved memory.

ORIGINAL RESPONSE:
{response}

UNSUPPORTED CLAIMS: {unsupported_claims}

Revise the response to remove or hedge these unsupported claims. Replace fabricated specifics with: "I don't have that in my memory." Do not add new claims. Keep everything else intact."""


def should_check_grounding(intent_class: str) -> bool:
    """Return True if this intent class should trigger a grounding check."""
    return intent_class in GROUNDING_CHECK_INTENTS


async def run_grounding_check(
    response: str,
    retrieved_context: str,
    model: str = "qwen3:8b",
) -> tuple[bool, str | None]:
    """
    Check if a response is grounded in retrieved context.

    Returns (is_grounded, unsupported_claims_or_none).
    Uses num_predict=50 and temperature=0 -- only needs YES/NO + brief claim list.
    """
    prompt = GROUNDING_CHECK_PROMPT.format(
        retrieved_context=retrieved_context,
        response=response,
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            result = await client.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                    "options": {"temperature": 0, "num_predict": 50},
                },
            )
        answer = result.json()["message"]["content"].strip()
        upper = answer.upper()

        if upper.startswith("YES"):
            logger.warning("[GROUNDING] Unsupported claims detected: %s", answer[:200])
            return False, answer
        return True, None

    except Exception as exc:
        logger.warning("[GROUNDING] Check failed (passing through): %s", exc)
        return True, None  # fail open -- don't block on grounding check errors


async def run_revision_pass(
    response: str,
    unsupported_claims: str,
    model: str = "qwen3:8b",
) -> str:
    """
    Revise a response to remove unsupported claims.
    """
    prompt = REVISION_PROMPT.format(
        response=response,
        unsupported_claims=unsupported_claims,
    )
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            result = await client.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
            )
        revised = result.json()["message"]["content"].strip()
        logger.warning("[GROUNDING] Revision pass completed")
        return revised
    except Exception as exc:
        logger.warning("[GROUNDING] Revision failed (returning original): %s", exc)
        return response  # fail open


def log_grounding_outcome(
    intent_class: str,
    triggered: bool,
    grounded: bool | None,
    revision_triggered: bool,
) -> None:
    """Log grounding check outcome alongside constitutional review logs."""
    try:
        log_dir = Path(__file__).resolve().parents[2] / "logs" / "safety_reviews"
        log_dir.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": datetime.now().isoformat(),
            "type": "grounding_check",
            "intent_class": intent_class,
            "triggered": triggered,
            "grounded": grounded,
            "revision_triggered": revision_triggered,
        }

        log_file = log_dir / f"{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}Z-grounding.json"
        log_file.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("[GROUNDING] Failed to log outcome: %s", exc)
