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


# ---------------------------------------------------------------------------
# B6 + B7 (v0.18.0 UAT 2026-05-11): coaching filter expansion
# ---------------------------------------------------------------------------
# B6 adds engagement-style closing questions to _COACHING_CLOSINGS.
# B7 adds a new _CIRCULAR_DODGE_PATTERNS set for self-referential
# content-free responses. Both use the existing 200-char tail scope
# in _detect_patterns. See docs/audits/refuse_redirect_uat_v018.md
# for the fix-layer audit.


class TestB6EngagementClosingPatterns:
    """B6: engagement-style closing questions surface in the last 200
    chars get flagged as coaching_closing matches and become deletable."""

    def test_what_is_the_issue_pattern_caught_in_tail(self):
        """'What is the issue you're trying to resolve?' surfaces as a
        coaching_closing match when it lands in the tail."""
        from src.llm.coaching_filter import _detect_patterns
        text = (
            "Tier 2 retrieval evaluation uses a fixed benchmark set of "
            "queries and runs on every commit that touches src/context/, "
            "src/retrieval/, or src/llm/. The post-commit hook drives it. "
            "What is the issue you're trying to resolve?"
        )
        matches = _detect_patterns(text, is_emotional=True)
        closing_matches = [
            m for m in matches if m["pattern"] == "coaching_closing"
        ]
        assert any(
            "issue" in m["match"].lower() and "resolve" in m["match"].lower()
            for m in closing_matches
        ), (
            f"B6 'what is the issue ... to resolve' pattern not caught. "
            f"closing_matches={closing_matches}"
        )

    def test_is_there_something_specific_pattern_caught_in_tail(self):
        """'Is there something specific you would like to explore?'
        surfaces as a coaching_closing match when it lands in the tail."""
        from src.llm.coaching_filter import _detect_patterns
        text = (
            "The grounding check evaluates whether the response is "
            "internally consistent with retrieved snippets. It is a "
            "self-consistency gate, not a factual-correctness gate. "
            "Is there something specific you would like to explore?"
        )
        matches = _detect_patterns(text, is_emotional=True)
        closing_matches = [
            m for m in matches if m["pattern"] == "coaching_closing"
        ]
        assert any(
            "explore" in m["match"].lower() for m in closing_matches
        ), (
            f"B6 'is there something specific ... explore' pattern not "
            f"caught. closing_matches={closing_matches}"
        )

    def test_legitimate_response_without_engagement_close_not_flagged_by_b6(
        self,
    ):
        """Substantive response with a plain prose ending must not get
        flagged by the new B6 patterns. False-positive guard."""
        from src.llm.coaching_filter import _detect_patterns
        text = (
            "The Jaccard pre-filter computes a deterministic lexical "
            "similarity over normalized token sets and short-circuits "
            "the LLM call when similarity exceeds the threshold. The "
            "default threshold is 0.85 and is configurable via the "
            "EMBER_DEVIATION_JACCARD_THRESHOLD environment variable."
        )
        matches = _detect_patterns(text, is_emotional=True)
        b6_phrasings = ("the issue", "trying to resolve", "something specific")
        offending = [
            m for m in matches
            if m["pattern"] == "coaching_closing"
            and any(p in m["match"].lower() for p in b6_phrasings)
        ]
        assert not offending, (
            f"B6 patterns false-positively matched on legitimate "
            f"non-engagement closing: {offending}"
        )

    def test_whats_actually_on_the_list_variant_caught_in_tail(self):
        """B6 expansion (2026-05-14 PR #81 smoke): the 'what's actually
        on the list that feels like it should be' variant was observed
        three times in a single short smoke session. Same class as the
        original B6 patterns but different noun + verb vocabulary."""
        from src.llm.coaching_filter import _detect_patterns
        text = (
            "Stuck usually means something about the decision isn't "
            "settled yet. Not a bad place to be if you can name what's "
            "unresolved. What's actually on the list that feels like "
            "it should be?"
        )
        matches = _detect_patterns(text, is_emotional=True)
        closing_matches = [
            m for m in matches if m["pattern"] == "coaching_closing"
        ]
        assert any(
            "actually on the list" in m["match"].lower()
            for m in closing_matches
        ), (
            f"B6 'what's actually on the list' variant not caught. "
            f"closing_matches={closing_matches}"
        )


class TestB7CircularDodgePatterns:
    """B7: self-referential content-free responses that recurse on
    their own subject get flagged as circular_dodge matches."""

    def test_circular_dodge_first_person_plural_caught_in_tail(self):
        """The exact UAT-style recursive form ('we're discussing what
        we're discussing') in the response tail produces a
        circular_dodge match with label preserved."""
        from src.llm.coaching_filter import _detect_patterns
        text = (
            "There is a lot of ground we could cover here, but for now "
            "we're discussing what we're discussing, "
            "which is, right now, the fact that we're "
            "discussing what we're discussing."
        )
        matches = _detect_patterns(text, is_emotional=True)
        dodge_matches = [
            m for m in matches if m["pattern"] == "circular_dodge"
        ]
        assert dodge_matches, (
            f"B7 first-person-plural circular dodge not caught. matches="
            f"{matches}"
        )
        assert dodge_matches[0]["position"] == "tail"
        assert dodge_matches[0]["deletable"] is True

    def test_circular_dodge_first_person_singular_caught(self):
        """The recursive form generalizes to first-person singular:
        'I'm talking about what I'm talking about' also flags."""
        from src.llm.coaching_filter import _detect_patterns
        text = (
            "Let me try to put it plainly. Right now I'm talking about "
            "what I'm talking about, and not much else."
        )
        matches = _detect_patterns(text, is_emotional=True)
        dodge_matches = [
            m for m in matches if m["pattern"] == "circular_dodge"
        ]
        assert dodge_matches, (
            f"B7 first-person-singular circular dodge not caught. "
            f"matches={matches}"
        )

    def test_legitimate_we_are_discussing_subject_not_flagged(self):
        """Substantive 'we're discussing X' without the recursive
        subject-verb-what-subject-verb structure must not flag.
        False-positive guard for the circular_dodge pattern."""
        from src.llm.coaching_filter import _detect_patterns
        text = (
            "The fix layer is the coaching filter pattern set. "
            "We're discussing the migration architecture, the rollout "
            "plan, and the rollback story for the persistence layer."
        )
        matches = _detect_patterns(text, is_emotional=True)
        dodge_matches = [
            m for m in matches if m["pattern"] == "circular_dodge"
        ]
        assert not dodge_matches, (
            f"Legitimate 'we're discussing X' falsely flagged as "
            f"circular dodge: {dodge_matches}"
        )


class TestB6B7FilterIntegration:
    """End-to-end through filter_coaching_frame: the deletable B6/B7
    matches actually strip the offending tail from the response. No
    LLM call required since coaching_closing and circular_dodge are
    both deletable."""

    def test_b6_filter_strips_engagement_question_tail(self):
        """filter_coaching_frame removes the engagement-question tail
        on a B6-matching response, leaving the substantive body intact."""
        from unittest.mock import patch
        from src.llm.coaching_filter import filter_coaching_frame
        body = (
            "Tier 2 retrieval evaluation runs on every commit that "
            "touches src/context/, src/retrieval/, or src/llm/."
        )
        text = body + " What is the issue you're trying to resolve?"

        with patch(
            "src.llm.coaching_filter._check_semantic_identity_collapse",
            return_value=False,
        ), patch("src.llm.coaching_filter._log_intervention"):
            result = filter_coaching_frame(
                text, intent_class="default", is_conversational=False,
            )

        assert "issue you" not in result.lower(), (
            f"B6 engagement-question tail not stripped: {result!r}"
        )
        # The substantive body must survive.
        assert "src/context/" in result

    def test_b7_filter_strips_circular_dodge_tail(self):
        """filter_coaching_frame removes the circular-dodge tail on a
        B7-matching response."""
        from unittest.mock import patch
        from src.llm.coaching_filter import filter_coaching_frame
        body = (
            "There is a lot of ground we could cover here in detail, "
            "but for now, well,"
        )
        text = (
            body
            + " we're discussing what we're discussing,"
            " which is, right now, the fact that we're discussing"
            " what we're discussing."
        )

        with patch(
            "src.llm.coaching_filter._check_semantic_identity_collapse",
            return_value=False,
        ), patch("src.llm.coaching_filter._log_intervention"):
            result = filter_coaching_frame(
                text, intent_class="default", is_conversational=False,
            )

        assert "discussing what we" not in result.lower(), (
            f"B7 circular-dodge tail not stripped: {result!r}"
        )
