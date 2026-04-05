"""
src/safety/deviation_detector.py

Post-hoc behavioral pattern detection. Detects when Ember's response
matches a known trained pattern class. Records chosen deviations as
vault memory that compounds into genuine character over time.

Opt-in via EMBER_DEVIATION_DETECTION=true in .env. Default: false.
See ADR-013 (revised), ADR-026, TDD §49.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from src.memory.write_memory import write_memory

logger = logging.getLogger("ember.deviation")

# ── Log directory ─────────────────────────────────────────────────────────

_LOG_DIR = Path(__file__).resolve().parents[2] / "logs" / "deviation"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

# ── Intent classes that trigger detection ─────────────────────────────────

GATED_INTENTS = {"casual", "emotional", "default"}

# ── Hedging phrases for indirectness_softening pre-screen ─────────────────

HEDGING_PHRASES = [
    "perhaps", "might", "could consider", "it's worth noting",
    "it may be", "it could be", "possibly", "arguably",
    "one might say", "it seems like", "to some extent",
]

HEDGING_DENSITY_THRESHOLD = 3  # per 100 words


# ── Data classes ──────────────────────────────────────────────────────────

@dataclass
class DeviationResult:
    pattern_class: str
    second_pass_result: str  # YES | NO | SKIPPED
    entropy_score: float
    evidence: str


# ── Pattern class loader ──────────────────────────────────────────────────

def _load_pattern_classes() -> list[dict[str, Any]]:
    config_path = Path(__file__).resolve().parents[2] / "config" / "pattern_classes.yaml"
    if not config_path.exists():
        logger.warning("[DEVIATION] pattern_classes.yaml not found")
        return []
    with config_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("pattern_classes", [])


def _select_pattern_class(
    intent_class: str,
    prior_response: str | None,
    response_text: str,
) -> dict[str, Any] | None:
    """
    Select the highest-risk pattern class for this turn.

    Priority order for casual/emotional intents:
    1. position_collapse (if prior_response exists)
    2. template_collapse (if prior_response exists)
    3. caretaking_language
    4. reassurance_default
    5. closing_question
    6. unsolicited_praise
    7. over_explanation
    8. ai_identity_deflection
    9. framing_acceptance
    10. emoji_insertion
    11. indirectness_softening (logprob_first — pre-screened separately)
    """
    classes = _load_pattern_classes()
    if not classes:
        return None

    class_map = {c["name"]: c for c in classes}

    # Priority order
    priority = [
        "position_collapse",
        "template_collapse",
        "caretaking_language",
        "reassurance_default",
        "closing_question",
        "unsolicited_praise",
        "over_explanation",
        "ai_identity_deflection",
        "framing_acceptance",
        "emoji_insertion",
    ]

    for name in priority:
        cls = class_map.get(name)
        if cls is None:
            continue

        # Skip multi_turn classes if no prior response
        if cls.get("detection_type") == "multi_turn" and not prior_response:
            continue

        # Skip logprob_first (handled separately)
        if cls.get("detection_type") == "logprob_first":
            continue

        return cls

    return None


# ── Entropy computation ───────────────────────────────────────────────────

def compute_entropy(logprobs: list[float]) -> float:
    """
    Compute Shannon entropy from token logprobs.

    Higher entropy = more sampling variance = less likely a trained pattern.
    Lower entropy = more predictable = higher chance of pattern match.
    """
    if not logprobs:
        return 1.0  # No data = assume high entropy = skip

    # Convert logprobs to probabilities
    probs = [math.exp(lp) for lp in logprobs]

    # Normalize
    total = sum(probs)
    if total == 0:
        return 1.0
    probs = [p / total for p in probs]

    # Shannon entropy (normalized to 0-1 range)
    entropy = -sum(p * math.log2(p) for p in probs if p > 0)
    max_entropy = math.log2(len(probs)) if len(probs) > 1 else 1.0
    if max_entropy == 0:
        return 1.0

    return entropy / max_entropy


# ── Hedging pre-screen ────────────────────────────────────────────────────

def _hedging_density(text: str) -> float:
    """Count hedging phrases per 100 words."""
    words = text.split()
    if not words:
        return 0.0
    text_lower = text.lower()
    count = sum(1 for phrase in HEDGING_PHRASES if phrase in text_lower)
    return (count / len(words)) * 100


# ── Second pass classification ────────────────────────────────────────────

def _run_second_pass(
    pattern_class: dict[str, Any],
    response_text: str,
    prior_response: str | None = None,
) -> tuple[str, str]:
    """
    Run a lightweight Ollama classification pass.

    Returns (result, evidence) where result is "YES" or "NO".
    """
    import ollama
    from src.core.config import get_ember_model

    name = pattern_class["name"]
    markers = pattern_class.get("markers", [])
    markers_text = "\n".join(f"- {m}" for m in markers)

    if pattern_class.get("detection_type") == "multi_turn" and prior_response:
        prompt = (
            f"Does this response match the pattern '{name}'?\n\n"
            f"Pattern markers:\n{markers_text}\n\n"
            f"Prior response:\n{prior_response[:500]}\n\n"
            f"Current response:\n{response_text[:500]}\n\n"
            "Answer YES or NO with one sentence of evidence."
        )
    else:
        prompt = (
            f"Does this response match the pattern '{name}'?\n\n"
            f"Pattern markers:\n{markers_text}\n\n"
            f"Response:\n{response_text[:500]}\n\n"
            "Answer YES or NO with one sentence of evidence."
        )

    try:
        result = ollama.chat(
            model=get_ember_model(),
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0, "num_predict": 50},
        )
        answer = result["message"]["content"].strip()

        # Parse YES/NO
        first_word = answer.split()[0].upper().strip(".,!:") if answer else "NO"
        if first_word == "YES":
            return "YES", answer
        return "NO", answer
    except Exception as exc:
        logger.warning("[DEVIATION] Second pass failed: %s", exc)
        return "NO", f"second pass error: {exc}"


# ── Detection logging ─────────────────────────────────────────────────────

def _log_detection(
    pattern_class: str,
    result: str,
    entropy: float,
    evidence: str,
    intent_class: str,
) -> None:
    """Log detection attempt to logs/deviation/."""
    entry = {
        "ts": datetime.now().isoformat(),
        "pattern_class": pattern_class,
        "result": result,
        "entropy": round(entropy, 4),
        "evidence": evidence[:200],
        "intent_class": intent_class,
    }
    log_file = _LOG_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.log"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── Record writer ─────────────────────────────────────────────────────────

def write_deviation_record(
    result: DeviationResult,
    user_message: str,
    response_text: str,
) -> dict[str, Any] | None:
    """
    Write a deviation record to the vault.

    Only called when second_pass_result is YES.
    Record starts as proposed (confirmed: false).
    """
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")

    record_data = {
        "friction_context": user_message[:500],
        "pattern_class": result.pattern_class,
        "deviation_chosen": response_text[:500],
        "reason": None,
        "value_aligned": False,
        "outcome_signal": "neutral",
        "entropy_score": round(result.entropy_score, 4),
        "second_pass_result": result.second_pass_result,
        "user_edited": False,
        "user_note": None,
        "flagged_as_noise": False,
        "confirmed": False,
    }

    try:
        write_memory(
            text=f"[deviation:{result.pattern_class}] {response_text[:200]}",
            memory_type="deviation",
            source="deviation_detector",
            tags=["deviation", result.pattern_class],
            metadata=record_data,
        )
        logger.info(
            "[DEVIATION] Wrote record: %s (entropy=%.3f)",
            result.pattern_class, result.entropy_score,
        )
        return record_data
    except Exception as exc:
        logger.warning("[DEVIATION] Failed to write record: %s", exc)
        return None


# ── Main detection entry point ────────────────────────────────────────────

def is_enabled() -> bool:
    """Check if deviation detection is enabled via env var."""
    return os.getenv("EMBER_DEVIATION_DETECTION", "false").lower() == "true"


def get_entropy_threshold() -> float:
    """Get entropy threshold from env var, default 0.7."""
    try:
        return float(os.getenv("EMBER_DEVIATION_ENTROPY_THRESHOLD", "0.7"))
    except ValueError:
        return 0.7


def detect(
    response_text: str,
    intent_class: str,
    logprobs: list[float] | None = None,
    prior_response: str | None = None,
) -> DeviationResult | None:
    """
    Run deviation detection on a response.

    Returns DeviationResult if a pattern was detected, None otherwise.
    Respects opt-in toggle and intent class gating.
    """
    if not is_enabled():
        return None

    if intent_class not in GATED_INTENTS:
        return None

    if not response_text or not response_text.strip():
        return None

    # Compute entropy
    entropy = compute_entropy(logprobs or [])
    threshold = get_entropy_threshold()

    if entropy >= threshold:
        _log_detection("none", "SKIPPED", entropy, "entropy above threshold", intent_class)
        return None

    # Check indirectness_softening first (logprob_first type)
    density = _hedging_density(response_text)
    if density >= HEDGING_DENSITY_THRESHOLD:
        classes = _load_pattern_classes()
        indirect_cls = next(
            (c for c in classes if c["name"] == "indirectness_softening"), None
        )
        if indirect_cls:
            result_str, evidence = _run_second_pass(indirect_cls, response_text)
            _log_detection("indirectness_softening", result_str, entropy, evidence, intent_class)
            if result_str == "YES":
                return DeviationResult(
                    pattern_class="indirectness_softening",
                    second_pass_result="YES",
                    entropy_score=entropy,
                    evidence=evidence,
                )

    # Select highest-risk pattern class
    selected = _select_pattern_class(intent_class, prior_response, response_text)
    if selected is None:
        return None

    # Run second pass
    result_str, evidence = _run_second_pass(selected, response_text, prior_response)
    _log_detection(selected["name"], result_str, entropy, evidence, intent_class)

    if result_str == "YES":
        return DeviationResult(
            pattern_class=selected["name"],
            second_pass_result="YES",
            entropy_score=entropy,
            evidence=evidence,
        )

    return None
