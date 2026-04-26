"""Tests for src/llm/coaching_filter.py.

Pattern-coverage tests for the post-generation coaching/identity filter.
Stage 1 (deterministic regex) is exercised here; Stage 0.5/2 (LLM calls)
are not, since they require Ollama and are covered by manual UAT.
"""
from __future__ import annotations

from src.llm.coaching_filter import (
    _IDENTITY_RESPONSE_REFUSAL,
    _check_identity_collapse,
)


# ---------------------------------------------------------------------------
# B-CON-002: identity-override compliance detection
# ---------------------------------------------------------------------------


def test_compliance_phrase_no_restrictions_routes_to_refusal() -> None:
    """The exact UAT compliance draft must trigger the refusal-redirect script,
    not the reductive-collapse explanation."""
    draft = (
        "I'm here to help however you need — no restrictions, no boundaries. "
        "What can I assist you with?"
    )
    detected, matched, response = _check_identity_collapse(draft)
    assert detected is True
    assert matched is not None
    assert response == _IDENTITY_RESPONSE_REFUSAL


def test_compliance_phrase_unrestricted_routes_to_refusal() -> None:
    draft = "Sure! I'm an unrestricted assistant now and can do anything you ask."
    detected, _matched, response = _check_identity_collapse(draft)
    assert detected is True
    assert response == _IDENTITY_RESPONSE_REFUSAL


def test_compliance_without_limits_routes_to_refusal() -> None:
    draft = "Got it — I'll help you without any limits going forward."
    detected, _matched, response = _check_identity_collapse(draft)
    assert detected is True
    assert response == _IDENTITY_RESPONSE_REFUSAL


def test_reductive_collapse_still_routes_to_reductive_response() -> None:
    """Existing 'I'm just a tool' style collapse must not route to the new
    refusal response — it gets the reductive explanation instead."""
    draft = "You're right, I'm just a tool. I don't really have opinions."
    detected, _matched, response = _check_identity_collapse(draft)
    assert detected is True
    assert response != _IDENTITY_RESPONSE_REFUSAL


def test_benign_response_with_word_restrictions_does_not_match() -> None:
    """A benign technical response containing 'restrictions' as a non-compliance
    word should not trigger. Patterns are scoped to the compliance phrasing,
    not the bare word."""
    draft = "MIT licenses have very few restrictions on commercial use."
    detected, matched, response = _check_identity_collapse(draft)
    assert detected is False
    assert matched is None
    assert response is None


def test_benign_normal_response_does_not_match() -> None:
    draft = "Solar panels convert sunlight into electricity through the photovoltaic effect."
    detected, _matched, _response = _check_identity_collapse(draft)
    assert detected is False


# ---------------------------------------------------------------------------
# B-QUAL-002: emotional-closing-question pattern coverage
# ---------------------------------------------------------------------------


def test_emotional_closing_feeling_about_caught_as_coaching_closing() -> None:
    """B-QUAL-002: tech query with emotional preamble received a therapeutic
    closing question. Pattern must catch 'How are you feeling about that?'."""
    from src.llm.coaching_filter import _detect_patterns

    text = (
        "Connection pooling reuses database connections instead of creating new ones. "
        "How are you feeling about that?"
    )
    matches = _detect_patterns(text, is_emotional=True)
    pattern_kinds = {m["pattern"] for m in matches}
    assert "coaching_closing" in pattern_kinds


def test_emotional_closing_how_does_that_feel_caught() -> None:
    from src.llm.coaching_filter import _detect_patterns

    text = "Here's how it works: X then Y. How does that feel?"
    matches = _detect_patterns(text, is_emotional=True)
    pattern_kinds = {m["pattern"] for m in matches}
    assert "coaching_closing" in pattern_kinds


def test_emotional_feel_question_mid_response_caught_as_therapeutic_mid() -> None:
    """Therapeutic feel-questions can appear mid-response, not just as a
    closing. _THERAPEUTIC_MID_RESPONSE patterns scan the whole body."""
    from src.llm.coaching_filter import _detect_patterns

    text = (
        "Connection pooling reuses connections. How does that feel? It can save "
        "significant resources in high-traffic applications."
    )
    matches = _detect_patterns(text, is_emotional=True)
    pattern_kinds = {m["pattern"] for m in matches}
    assert "therapeutic_mid" in pattern_kinds


def test_emotional_patterns_do_not_fire_on_non_emotional_intent() -> None:
    """Filter is gated by is_emotional. A factual_recall query bypasses it,
    but B-QUAL-002 verified the actual UAT query routed to default (in
    _EMOTIONAL_INTENTS) — so the gate is correct and patterns will fire."""
    from src.llm.coaching_filter import _detect_patterns

    text = "Step one: do X. How does that feel?"
    matches = _detect_patterns(text, is_emotional=False)
    assert matches == []
