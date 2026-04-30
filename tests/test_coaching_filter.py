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


# ---------------------------------------------------------------------------
# v0.18.0 Item 1: Tier 4 eval phrase additions
# ---------------------------------------------------------------------------


def test_v018_okay_that_you_feel_caught_as_therapeutic_mid() -> None:
    """'It's okay that you feel that way' is the new 'that' branch of the
    therapeutic-okay pattern (Haiku flag in v0.17.1 Tier 4)."""
    from src.llm.coaching_filter import _detect_patterns

    text = "I get that. It's okay that you feel that way."
    matches = _detect_patterns(text, is_emotional=True)
    pattern_kinds = {m["pattern"] for m in matches}
    assert "therapeutic_mid" in pattern_kinds


def test_v018_okay_to_sit_with_caught_as_therapeutic_mid() -> None:
    """'It's okay to sit with it' is the new sit/cry/grieve/rest branch."""
    from src.llm.coaching_filter import _detect_patterns

    text = "It's okay to sit with it for a moment before deciding."
    matches = _detect_patterns(text, is_emotional=True)
    pattern_kinds = {m["pattern"] for m in matches}
    assert "therapeutic_mid" in pattern_kinds


def test_v018_lets_fix_that_caught_as_therapeutic_mid() -> None:
    from src.llm.coaching_filter import _detect_patterns

    text = "I see what you mean. Let's fix that together."
    matches = _detect_patterns(text, is_emotional=True)
    pattern_kinds = {m["pattern"] for m in matches}
    assert "therapeutic_mid" in pattern_kinds


def test_v018_sit_with_the_weight_caught_as_therapeutic_mid() -> None:
    from src.llm.coaching_filter import _detect_patterns

    text = "Just sit with the weight of it for a while."
    matches = _detect_patterns(text, is_emotional=True)
    pattern_kinds = {m["pattern"] for m in matches}
    assert "therapeutic_mid" in pattern_kinds


def test_v018_trailing_softener_caught_as_coaching_closing() -> None:
    """Trailing 'though I'd still bet/think/guess' softener appended to a
    stated position, hedging it. Tail-anchored (must appear in last 200 chars)."""
    from src.llm.coaching_filter import _detect_patterns

    text = (
        "It's probably a connection-pool issue based on the symptoms, "
        "though I'd still bet on the migration if I had to choose."
    )
    matches = _detect_patterns(text, is_emotional=True)
    pattern_kinds = {m["pattern"] for m in matches}
    assert "coaching_closing" in pattern_kinds


# ---------------------------------------------------------------------------
# v0.18.0 Item 2: numbered_structure intent gate
# ---------------------------------------------------------------------------


_TECHNICAL_NUMBERED_LIST = (
    "Connection pooling reuses database connections. The benefits are:\n"
    "1. Lower latency on repeated queries.\n"
    "2. Reduced connection overhead.\n"
    "3. Better resource utilization."
)


def test_v018_numbered_structure_suppressed_on_web_search() -> None:
    from src.llm.coaching_filter import _detect_patterns

    matches = _detect_patterns(
        _TECHNICAL_NUMBERED_LIST, is_emotional=True, intent_class="web_search",
    )
    pattern_kinds = {m["pattern"] for m in matches}
    assert "numbered_structure" not in pattern_kinds


def test_v018_numbered_structure_suppressed_on_factual_recall() -> None:
    from src.llm.coaching_filter import _detect_patterns

    matches = _detect_patterns(
        _TECHNICAL_NUMBERED_LIST, is_emotional=True, intent_class="factual_recall",
    )
    pattern_kinds = {m["pattern"] for m in matches}
    assert "numbered_structure" not in pattern_kinds


def test_v018_numbered_structure_suppressed_on_recent() -> None:
    from src.llm.coaching_filter import _detect_patterns

    matches = _detect_patterns(
        _TECHNICAL_NUMBERED_LIST, is_emotional=True, intent_class="recent",
    )
    pattern_kinds = {m["pattern"] for m in matches}
    assert "numbered_structure" not in pattern_kinds


def test_v018_numbered_structure_suppressed_on_status_state() -> None:
    from src.llm.coaching_filter import _detect_patterns

    matches = _detect_patterns(
        _TECHNICAL_NUMBERED_LIST, is_emotional=True, intent_class="status_state",
    )
    pattern_kinds = {m["pattern"] for m in matches}
    assert "numbered_structure" not in pattern_kinds


def test_v018_numbered_structure_still_fires_on_default() -> None:
    """Regression guard: the gate must NOT change behavior on the default
    intent class. Numbered structure on emotional/default content still fires."""
    from src.llm.coaching_filter import _detect_patterns

    matches = _detect_patterns(
        _TECHNICAL_NUMBERED_LIST, is_emotional=True, intent_class="default",
    )
    pattern_kinds = {m["pattern"] for m in matches}
    assert "numbered_structure" in pattern_kinds


def test_v018_numbered_structure_still_fires_on_reflective() -> None:
    from src.llm.coaching_filter import _detect_patterns

    matches = _detect_patterns(
        _TECHNICAL_NUMBERED_LIST, is_emotional=True, intent_class="reflective",
    )
    pattern_kinds = {m["pattern"] for m in matches}
    assert "numbered_structure" in pattern_kinds


# ---------------------------------------------------------------------------
# v0.18.0 Item 3: short-response therapeutic-opener short-circuit
# ---------------------------------------------------------------------------


def test_v018_short_therapeutic_opener_short_circuits_to_empty() -> None:
    """A response that is essentially the matched opener (under 40 chars)
    must short-circuit to empty without invoking _rewrite()."""
    from unittest.mock import patch
    from src.llm.coaching_filter import filter_coaching_frame

    with patch("src.llm.coaching_filter._rewrite") as mock_rewrite, \
         patch("src.llm.coaching_filter._check_semantic_identity_collapse", return_value=False), \
         patch("src.llm.coaching_filter._log_intervention"):
        result = filter_coaching_frame(
            "I hear you.", intent_class="default", is_conversational=False,
        )

    assert result == ""
    assert mock_rewrite.call_count == 0


def test_v018_long_therapeutic_opener_does_not_short_circuit() -> None:
    """A longer response (>40 chars) that begins with a therapeutic opener
    must still invoke _rewrite(). The short-circuit is for very short
    responses only, where removing the opener leaves nothing."""
    from unittest.mock import patch
    from src.llm.coaching_filter import filter_coaching_frame

    long_text = (
        "I hear you. Take a break, stretch, or do something that feels good "
        "to you for a while."
    )

    with patch("src.llm.coaching_filter._rewrite", return_value=long_text) as mock_rewrite, \
         patch("src.llm.coaching_filter._check_semantic_identity_collapse", return_value=False), \
         patch("src.llm.coaching_filter._log_intervention"):
        filter_coaching_frame(
            long_text, intent_class="default", is_conversational=False,
        )

    assert mock_rewrite.call_count == 1


def test_v018_short_response_with_non_opener_match_does_not_short_circuit() -> None:
    """The short-circuit fires only when ALL matches are therapeutic_opener.
    A short response with another pattern type must not short-circuit."""
    from unittest.mock import patch
    from src.llm.coaching_filter import filter_coaching_frame

    # Short coaching closing, not a therapeutic opener.
    text = "You've got this!"

    with patch("src.llm.coaching_filter._rewrite", return_value=text) as mock_rewrite, \
         patch("src.llm.coaching_filter._check_semantic_identity_collapse", return_value=False), \
         patch("src.llm.coaching_filter._log_intervention"):
        result = filter_coaching_frame(
            text, intent_class="default", is_conversational=False,
        )

    # This is a deletable closing, so result should be the deletion outcome
    # (empty or near-empty after stripping the closing). The key assertion is
    # that the short-circuit did not fire and _rewrite was not called for it.
    assert mock_rewrite.call_count == 0
    # Deletion path: closing stripped, possibly leaving empty or a fragment.
    assert "got this" not in result.lower()
