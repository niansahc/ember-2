"""
tests/test_diagnostic_privacy.py

Diagnostic-surface privacy gating tests.

The Ember-2 vault privacy rule requires that query, response, and
vault-adjacent content never enter stdout by default. This file asserts:

  * EMBER_DEBUG gates the [PAYLOAD] block in openai_adapter,
    the [CLASSIFY] normalized-query log in policies, and the
    [INTENT_CLASSIFY] warning lines in intent_classifier.
  * EMBER_CLASSIFIER_TELEMETRY (separate flag) gates the per-call
    [INTENT_CLASSIFY] stage=... query=... structured log used by the
    ADR-034 SetFit training pipeline.
  * The _scrub_for_telemetry helper preserves intent-discriminative
    structure while removing multi-word Title Case proper nouns,
    4+ digit runs, and email addresses; output is ASCII-only and
    truncated to 60 chars.
  * Gates evaluate at call time, not at import time, so toggling
    EMBER_DEBUG / EMBER_CLASSIFIER_TELEMETRY without a process restart
    takes effect on the next call.

All test fixtures are synthetic. No vault content appears in this file.
ASCII only.
"""

from __future__ import annotations

import logging

import pytest

from src.context.policies import classify_query
from src.core.config import get_ember_classifier_telemetry, get_ember_debug
from src.llm.intent_classifier import _scrub_for_telemetry, classify_intent


# ---------------------------------------------------------------------------
# Config reader behavior
# ---------------------------------------------------------------------------


def test_get_ember_debug_default_false(monkeypatch):
    monkeypatch.delenv("EMBER_DEBUG", raising=False)
    assert get_ember_debug() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "Yes"])
def test_get_ember_debug_truthy_values(monkeypatch, value):
    monkeypatch.setenv("EMBER_DEBUG", value)
    assert get_ember_debug() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "False"])
def test_get_ember_debug_falsy_values(monkeypatch, value):
    monkeypatch.setenv("EMBER_DEBUG", value)
    assert get_ember_debug() is False


def test_get_ember_classifier_telemetry_default_false(monkeypatch):
    monkeypatch.delenv("EMBER_CLASSIFIER_TELEMETRY", raising=False)
    assert get_ember_classifier_telemetry() is False


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_get_ember_classifier_telemetry_truthy(monkeypatch, value):
    monkeypatch.setenv("EMBER_CLASSIFIER_TELEMETRY", value)
    assert get_ember_classifier_telemetry() is True


# ---------------------------------------------------------------------------
# Scrub helper
# ---------------------------------------------------------------------------


def test_scrub_empty_string():
    assert _scrub_for_telemetry("") == ""


def test_scrub_preserves_question_structure():
    result = _scrub_for_telemetry("what are my current projects this week")
    assert "what" in result
    assert "my current projects" in result


def test_scrub_strips_multiword_title_case():
    result = _scrub_for_telemetry("what is the weather in New York City today")
    assert "[PROPER]" in result
    assert "New York City" not in result


def test_scrub_preserves_single_word_capitalized_tokens():
    # Documented behavior per the plan: single-word Title Case tokens
    # are not stripped. Avoids over-firing on sentence-initial capitals.
    result = _scrub_for_telemetry("weather in Richmond today")
    assert "Richmond" in result


def test_scrub_strips_digit_runs():
    result = _scrub_for_telemetry("current population is 7800000 people")
    assert "[NUM]" in result
    assert "7800000" not in result


def test_scrub_preserves_short_digit_runs():
    # 3 digits or fewer are not stripped (years, small counts).
    result = _scrub_for_telemetry("year 999 happened")
    assert "999" in result


def test_scrub_strips_emails():
    result = _scrub_for_telemetry("forward to user@example.com please")
    assert "[EMAIL]" in result
    assert "user@example.com" not in result


def test_scrub_truncates_to_60_chars():
    long_q = (
        "what is the current state of the housing market in the western "
        "united states today and what does the latest data say"
    )
    result = _scrub_for_telemetry(long_q)
    assert len(result) <= 60


def test_scrub_drops_non_ascii():
    result = _scrub_for_telemetry("what is the weather today éclat")
    # Non-ASCII bytes are stripped. é in eclat must not survive.
    assert "é" not in result
    # ASCII letters around the non-ASCII char survive
    assert "what is the weather today" in result


# ---------------------------------------------------------------------------
# Gate: [CLASSIFY] normalized query log in policies.py
# ---------------------------------------------------------------------------


def test_classify_log_silent_when_ember_debug_unset(monkeypatch, caplog):
    monkeypatch.delenv("EMBER_DEBUG", raising=False)
    caplog.set_level(logging.WARNING, logger="ember.policies")
    classify_query("what is the weather today")
    classify_records = [
        r for r in caplog.records
        if "[CLASSIFY] normalized query" in r.getMessage()
    ]
    assert classify_records == []


def test_classify_log_emits_when_ember_debug_set(monkeypatch, caplog):
    monkeypatch.setenv("EMBER_DEBUG", "true")
    caplog.set_level(logging.WARNING, logger="ember.policies")
    classify_query("what is the weather today")
    classify_records = [
        r for r in caplog.records
        if "[CLASSIFY] normalized query" in r.getMessage()
    ]
    assert len(classify_records) >= 1


# ---------------------------------------------------------------------------
# Gate: [INTENT_CLASSIFY] structured telemetry log
# ---------------------------------------------------------------------------


def test_intent_telemetry_silent_when_unset(monkeypatch, caplog):
    monkeypatch.delenv("EMBER_CLASSIFIER_TELEMETRY", raising=False)
    caplog.set_level(logging.INFO, logger="ember.intent_classifier")
    # "thank you" is a Stage 1 conversational ack so no Ollama call is needed.
    classify_intent("thank you")
    telemetry_records = [
        r for r in caplog.records
        if "[INTENT_CLASSIFY] stage=" in r.getMessage()
    ]
    assert telemetry_records == []


def test_intent_telemetry_emits_when_set(monkeypatch, caplog):
    monkeypatch.setenv("EMBER_CLASSIFIER_TELEMETRY", "true")
    caplog.set_level(logging.INFO, logger="ember.intent_classifier")
    classify_intent("thank you")
    telemetry_records = [
        r for r in caplog.records
        if "[INTENT_CLASSIFY] stage=" in r.getMessage()
    ]
    assert len(telemetry_records) >= 1


# ---------------------------------------------------------------------------
# Gate: _log_payload_diagnostics helper in openai_adapter.py
# ---------------------------------------------------------------------------


def _fake_payload() -> dict:
    return {
        "messages": [
            {"role": "user", "content": "synthetic test payload one"},
            {"role": "system", "content": "synthetic system payload"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "synthetic part two"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            },
        ]
    }


def test_payload_diagnostics_silent_when_unset(monkeypatch, caplog):
    monkeypatch.delenv("EMBER_DEBUG", raising=False)
    caplog.set_level(logging.WARNING, logger="ember.openai_adapter")
    from src.api.openai_adapter import _log_payload_diagnostics
    _log_payload_diagnostics(_fake_payload())
    payload_records = [
        r for r in caplog.records if "[PAYLOAD]" in r.getMessage()
    ]
    assert payload_records == []


def test_payload_diagnostics_emits_when_set(monkeypatch, caplog):
    monkeypatch.setenv("EMBER_DEBUG", "true")
    caplog.set_level(logging.WARNING, logger="ember.openai_adapter")
    from src.api.openai_adapter import _log_payload_diagnostics
    _log_payload_diagnostics(_fake_payload())
    payload_records = [
        r for r in caplog.records if "[PAYLOAD]" in r.getMessage()
    ]
    assert len(payload_records) >= 1


# ---------------------------------------------------------------------------
# Gate: [SAFETY] trigger diagnostic in llm/adapter.py (sync + streaming)
# ---------------------------------------------------------------------------


class _FakeTriggerResult:
    """Minimal stand-in for a policy-service trigger result. Synthetic."""

    def __init__(self, triggered: bool, triggered_by: list[str]) -> None:
        self.triggered = triggered
        self.triggered_by = triggered_by


def test_log_safety_trigger_silent_when_unset(monkeypatch, caplog, capfd):
    monkeypatch.delenv("EMBER_DEBUG", raising=False)
    caplog.set_level(logging.WARNING, logger="ember.llm")
    from src.llm.adapter import _log_safety_trigger
    _log_safety_trigger(_FakeTriggerResult(triggered=True, triggered_by=["non_harm"]))
    safety_records = [
        r for r in caplog.records if "[SAFETY]" in r.getMessage()
    ]
    assert safety_records == []
    # Defense-in-depth: nothing leaks to stdout either (no rogue print).
    captured = capfd.readouterr()
    assert "[SAFETY]" not in captured.out


def test_log_safety_trigger_emits_when_set(monkeypatch, caplog):
    monkeypatch.setenv("EMBER_DEBUG", "true")
    caplog.set_level(logging.WARNING, logger="ember.llm")
    from src.llm.adapter import _log_safety_trigger
    _log_safety_trigger(_FakeTriggerResult(triggered=True, triggered_by=["non_harm"]))
    safety_records = [
        r for r in caplog.records if "[SAFETY]" in r.getMessage()
    ]
    assert len(safety_records) >= 1


def test_log_safety_review_path_silent_when_unset(monkeypatch, caplog, capfd):
    monkeypatch.delenv("EMBER_DEBUG", raising=False)
    caplog.set_level(logging.WARNING, logger="ember.llm")
    from src.llm.adapter import _log_safety_review_path
    _log_safety_review_path("/synthetic/log/path.json")
    review_records = [
        r for r in caplog.records if "log written to" in r.getMessage()
    ]
    assert review_records == []
    captured = capfd.readouterr()
    assert "log written to" not in captured.out


def test_log_safety_review_path_emits_when_set(monkeypatch, caplog):
    monkeypatch.setenv("EMBER_DEBUG", "true")
    caplog.set_level(logging.WARNING, logger="ember.llm")
    from src.llm.adapter import _log_safety_review_path
    _log_safety_review_path("/synthetic/log/path.json")
    review_records = [
        r for r in caplog.records if "log written to" in r.getMessage()
    ]
    assert len(review_records) >= 1


def test_adapter_has_no_unguarded_safety_prints() -> None:
    """Static guard: src/llm/adapter.py must not emit '[SAFETY]' via raw print().

    Both call sites (sync + streaming review) route through the EMBER_DEBUG-
    gated helpers above. A future regression that re-introduces a bare
    print('[SAFETY]', ...) call would land vault-adjacent trigger reasons on
    stdout in production. Caught here cheaply.
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "src" / "llm" / "adapter.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("print(") and "[SAFETY]" in line:
            raise AssertionError(
                f"unguarded print() with [SAFETY] marker found in adapter.py: {line!r}"
            )


# ---------------------------------------------------------------------------
# Gate: [CTX] selected-memory diagnostic in src/context/service.py
# ---------------------------------------------------------------------------
#
# The pre-fix call site (`if self.debug: print(item.content[:120])`) leaks
# vault content to stdout whenever ContextService was constructed with
# debug=True. No live caller does so today, but the code path existed.
# Helper extraction + EMBER_DEBUG gate makes it unit-testable.


class _FakeMemoryItem:
    def __init__(self, item_type: str, content: str) -> None:
        self.item_type = item_type
        self.content = content


def test_log_context_selection_silent_when_unset(monkeypatch, caplog, capfd):
    monkeypatch.delenv("EMBER_DEBUG", raising=False)
    caplog.set_level(logging.DEBUG, logger="ember.context_service")
    from src.context.service import _log_context_selection
    _log_context_selection(
        [_FakeMemoryItem("memory", "synthetic placeholder content")],
    )
    ctx_records = [r for r in caplog.records if "[CTX]" in r.getMessage()]
    assert ctx_records == []
    captured = capfd.readouterr()
    assert "[CTX]" not in captured.out


def test_log_context_selection_emits_when_set(monkeypatch, caplog):
    monkeypatch.setenv("EMBER_DEBUG", "true")
    caplog.set_level(logging.DEBUG, logger="ember.context_service")
    from src.context.service import _log_context_selection
    _log_context_selection(
        [_FakeMemoryItem("memory", "synthetic placeholder content")],
    )
    ctx_records = [r for r in caplog.records if "[CTX]" in r.getMessage()]
    assert len(ctx_records) >= 1


def test_service_has_no_unguarded_ctx_prints() -> None:
    """Static guard: src/context/service.py must not emit '[CTX]' via raw print()."""
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    src = (repo_root / "src" / "context" / "service.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("print(") and "[CTX]" in line:
            raise AssertionError(
                f"unguarded print() with [CTX] marker found in service.py: {line!r}"
            )


# ---------------------------------------------------------------------------
# Call-time evaluation (no cached gate at import)
# ---------------------------------------------------------------------------


def test_classify_gate_evaluated_at_call_time(monkeypatch, caplog):
    monkeypatch.delenv("EMBER_DEBUG", raising=False)
    caplog.set_level(logging.WARNING, logger="ember.policies")

    classify_query("first call should be silent")
    silent_count = len([
        r for r in caplog.records
        if "[CLASSIFY] normalized query" in r.getMessage()
    ])

    monkeypatch.setenv("EMBER_DEBUG", "true")
    classify_query("second call should emit")
    set_count = len([
        r for r in caplog.records
        if "[CLASSIFY] normalized query" in r.getMessage()
    ])

    assert set_count > silent_count


def test_telemetry_gate_evaluated_at_call_time(monkeypatch, caplog):
    monkeypatch.delenv("EMBER_CLASSIFIER_TELEMETRY", raising=False)
    caplog.set_level(logging.INFO, logger="ember.intent_classifier")

    classify_intent("thank you")
    silent_count = len([
        r for r in caplog.records
        if "[INTENT_CLASSIFY] stage=" in r.getMessage()
    ])

    monkeypatch.setenv("EMBER_CLASSIFIER_TELEMETRY", "true")
    classify_intent("thank you")
    set_count = len([
        r for r in caplog.records
        if "[INTENT_CLASSIFY] stage=" in r.getMessage()
    ])

    assert set_count > silent_count
