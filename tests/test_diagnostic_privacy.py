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
