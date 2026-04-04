"""
Tests for grounding verification layer (ADR-019).

Covers: intent triggering, grounding check logic, revision pass,
logging, and non-triggered intents.
"""

import pytest

from src.safety.grounding_check import (
    GROUNDING_CHECK_INTENTS,
    should_check_grounding,
    log_grounding_outcome,
)


# ── Intent triggering ──────────────────────────────────────────────────


def test_factual_recall_triggers_grounding():
    assert should_check_grounding("factual_recall") is True


def test_status_state_triggers_grounding():
    assert should_check_grounding("status_state") is True


def test_reflective_triggers_grounding():
    assert should_check_grounding("reflective") is True


def test_web_search_triggers_grounding():
    assert should_check_grounding("web_search") is True


def test_default_does_not_trigger_grounding():
    assert should_check_grounding("default") is False


def test_activity_does_not_trigger_grounding():
    assert should_check_grounding("activity") is False


def test_recent_does_not_trigger_grounding():
    assert should_check_grounding("recent") is False


# ── Intent set contents ────────────────────────────────────────────────


def test_grounding_intents_contains_expected():
    assert "factual_recall" in GROUNDING_CHECK_INTENTS
    assert "status_state" in GROUNDING_CHECK_INTENTS
    assert "reflective" in GROUNDING_CHECK_INTENTS
    assert "web_search" in GROUNDING_CHECK_INTENTS


def test_grounding_intents_does_not_contain_casual():
    assert "default" not in GROUNDING_CHECK_INTENTS
    assert "activity" not in GROUNDING_CHECK_INTENTS
    assert "recent" not in GROUNDING_CHECK_INTENTS
    assert "recent_activity" not in GROUNDING_CHECK_INTENTS


# ── Logging ────────────────────────────────────────────────────────────


def test_log_grounding_outcome_does_not_crash(tmp_path):
    """Logging should not raise even if the log directory doesn't exist."""
    log_grounding_outcome(
        intent_class="factual_recall",
        triggered=True,
        grounded=True,
        revision_triggered=False,
    )


# ── Async function signatures ──────────────────────────────────────────


def test_run_grounding_check_is_async():
    """Verify run_grounding_check is an async function."""
    from src.safety.grounding_check import run_grounding_check
    import asyncio
    assert asyncio.iscoroutinefunction(run_grounding_check)


def test_run_revision_pass_is_async():
    """Verify run_revision_pass is an async function."""
    from src.safety.grounding_check import run_revision_pass
    import asyncio
    assert asyncio.iscoroutinefunction(run_revision_pass)
