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


# ---------------------------------------------------------------------------
# Fix 2 (2026-04-27): span-based deletion preserves prior content
# ---------------------------------------------------------------------------


def test_span_deletion_preserves_prior_sentence_when_closing_is_appended() -> None:
    """The common case: coaching closing is its own appended sentence.
    Prior sentences must remain intact."""
    from src.llm.coaching_filter import _apply_deletions

    text = (
        "Sensitivity is important when talking about loss, especially when "
        "navigating grief. Let me know if you want to talk about it."
    )
    matches = [{
        "pattern": "coaching_closing",
        "match": "let me know if you want",
        "position": "tail",
        "deletable": True,
    }]
    result = _apply_deletions(text, matches)
    # Prior content must remain — both clauses joined by "especially when"
    # should survive the deletion.
    assert "Sensitivity is important when talking about loss" in result
    assert "especially when navigating grief" in result
    # The closing must be removed.
    assert "let me know" not in result.lower()


def test_span_deletion_preserves_prior_clause_when_closing_is_trailing() -> None:
    """Regression for the UAT mid-sentence truncation: when a coaching
    closing is appended as a trailing clause (after a comma or em-dash),
    the prior clause must NOT be dropped along with it."""
    from src.llm.coaching_filter import _apply_deletions

    text = (
        "Sensitivity is important when talking about loss, "
        "let me know if you want to talk."
    )
    matches = [{
        "pattern": "coaching_closing",
        "match": "let me know if you want",
        "position": "tail",
        "deletable": True,
    }]
    result = _apply_deletions(text, matches)
    # Prior clause survives, including "important when talking about loss"
    assert "Sensitivity is important when talking about loss" in result
    # The trailing comma is dropped along with the coaching closing
    assert not result.endswith(",")
    assert "let me know" not in result.lower()


def test_span_deletion_drops_em_dash_connector() -> None:
    from src.llm.coaching_filter import _apply_deletions

    text = "I think the cat is fine — let me know if you want help."
    matches = [{
        "pattern": "coaching_closing",
        "match": "let me know",
        "position": "tail",
        "deletable": True,
    }]
    result = _apply_deletions(text, matches)
    assert result == "I think the cat is fine"


def test_span_deletion_no_match_returns_text_unchanged() -> None:
    """Defensive: if the match string isn't actually in the text (stale
    match dict), the result is the original text (rstripped)."""
    from src.llm.coaching_filter import _apply_deletions

    text = "A perfectly fine response."
    matches = [{
        "pattern": "coaching_closing",
        "match": "ghost phrase that is not in text",
        "position": "tail",
        "deletable": True,
    }]
    result = _apply_deletions(text, matches)
    assert result == text


def test_span_deletion_skips_non_deletable_matches() -> None:
    """Non-deletable patterns (rewrites) are not handled by _apply_deletions;
    they pass through unchanged for Stage 2."""
    from src.llm.coaching_filter import _apply_deletions

    text = "Some content. Another thought."
    matches = [{
        "pattern": "therapeutic_opener",
        "match": "Some content",
        "position": "head",
        "deletable": False,
    }]
    result = _apply_deletions(text, matches)
    assert result == text


# ---------------------------------------------------------------------------
# Fix 2 Part B: explicit num_predict on primary Ollama calls
# ---------------------------------------------------------------------------


def test_chat_ollama_passes_num_predict_to_options() -> None:
    """Primary _chat_ollama must pass num_predict=2048 explicitly so Ollama
    runtime defaults can't silently cap output."""
    from unittest.mock import patch
    from src.llm.adapter import LLMAdapter

    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "qwen3:8b"

    captured: dict = {}

    def _capture_chat(*, model, messages, options, stream=False):
        captured["options"] = options
        if stream:
            return iter([{"message": {"content": "ok"}}])
        return {"message": {"content": "ok"}}

    with patch("src.llm.adapter.ollama.chat", side_effect=_capture_chat), \
         patch("src.core.preferences.get", return_value=None):
        adapter._chat_ollama(
            system_prompt="sys", user_message="msg",
            image_data=None, model="qwen3:8b", temperature=0.5,
        )

    assert "num_predict" in captured["options"]
    assert captured["options"]["num_predict"] >= 2048


def test_chat_ollama_stream_passes_num_predict_to_options() -> None:
    """Same contract for the streaming path."""
    from unittest.mock import patch
    from src.llm.adapter import LLMAdapter

    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "qwen3:8b"

    captured: dict = {}

    def _capture_chat(*, model, messages, options, stream=False):
        captured["options"] = options
        return iter([{"message": {"content": "ok"}}])

    with patch("src.llm.adapter.ollama.chat", side_effect=_capture_chat), \
         patch("src.core.preferences.get", return_value=None):
        # Drain the generator to actually invoke the call
        list(adapter._chat_ollama_stream(
            system_prompt="sys", user_message="msg",
            image_data=None, model="qwen3:8b", temperature=0.5,
        ))

    assert "num_predict" in captured["options"]
    assert captured["options"]["num_predict"] >= 2048
