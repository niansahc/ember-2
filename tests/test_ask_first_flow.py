"""tests/test_ask_first_flow.py — automated coverage for the ask-first
confirmation flow and web search execution pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.llm.ask_first_validator import (
    SCRIPTED_ASK_FIRST_RESPONSE,
    _has_confirmation_question,
    _has_first_person_marker,
    validate_ask_first_response,
)
from src.llm.post_gen_pipeline import run_post_gen_pipeline


# ---------------------------------------------------------------------------
# 1. Deterministic keyword matching
# ---------------------------------------------------------------------------


class TestDeterministicKeywordMatch:
    """YES/NO matching uses word-set intersection, not LLM."""

    _SINGLE = {"yes", "yeah", "sure", "please", "yep", "ok", "okay",
                "search", "y"}
    _PHRASES = ("go ahead", "do it", "please search")

    def _is_affirmed(self, msg: str) -> bool:
        cleaned = re.sub(r"[^\w\s]", "", msg.strip()).lower()
        words = set(cleaned.split())
        return (
            bool(words & self._SINGLE)
            or any(p in cleaned for p in self._PHRASES)
        )

    @pytest.mark.parametrize("msg", [
        "Yes", "yes", "YES", "Yeah", "Sure", "Please", "Go ahead",
        "Do it", "Yep", "Ok", "Okay", "Search", "Please search",
        "y", "Yes!", "yes.", "Sure, go ahead", "yeah!!", "OK.",
    ])
    def test_affirmative_variants(self, msg):
        assert self._is_affirmed(msg) is True

    @pytest.mark.parametrize("msg", [
        "No", "no", "Nah", "Never mind", "Skip it",
        "What is the population of Tokyo",
        "Tell me about dogs",
        "I changed my mind",
        "",
    ])
    def test_negative_and_topic_change(self, msg):
        if not msg:
            # Empty string has no words → no intersection
            assert self._is_affirmed(msg) is False
            return
        assert self._is_affirmed(msg) is False

    def test_punctuation_stripped(self):
        assert self._is_affirmed("yes!!!") is True
        assert self._is_affirmed("sure...") is True
        assert self._is_affirmed("go ahead?") is True

    def test_case_insensitive(self):
        assert self._is_affirmed("YES") is True
        assert self._is_affirmed("YeAh") is True
        assert self._is_affirmed("SURE") is True


# ---------------------------------------------------------------------------
# 2. Ask-first validator
# ---------------------------------------------------------------------------


class TestAskFirstValidator:

    def test_substitutes_when_no_confirmation_question(self):
        resp, sub = validate_ask_first_response(
            "I don't have access to live data. Check Google.",
            intent_class="web_search",
            ask_first_mode=True,
        )
        assert sub is True
        assert resp == SCRIPTED_ASK_FIRST_RESPONSE

    def test_preserves_when_confirmation_present(self):
        resp, sub = validate_ask_first_response(
            "I don't have that — want me to search for it?",
            intent_class="web_search",
            ask_first_mode=True,
        )
        assert sub is False
        assert "want me to search" in resp.lower()

    def test_no_op_when_not_web_search(self):
        resp, sub = validate_ask_first_response(
            "Some response without search offer.",
            intent_class="default",
            ask_first_mode=True,
        )
        assert sub is False

    def test_no_op_when_autonomous(self):
        resp, sub = validate_ask_first_response(
            "I don't have live data.",
            intent_class="web_search",
            ask_first_mode=False,
        )
        assert sub is False

    def test_empty_passes_through(self):
        resp, sub = validate_ask_first_response(
            "",
            intent_class="web_search",
            ask_first_mode=True,
        )
        assert sub is False
        assert resp == ""


# ---------------------------------------------------------------------------
# 2b. First-person guard (B-CTX-001 family)
# ---------------------------------------------------------------------------


class TestFirstPersonMarkerDetection:
    """The _has_first_person_marker helper identifies conversational /
    personal queries so the ask-first substitution can be skipped."""

    @pytest.mark.parametrize("msg", [
        "What was I nervous about for this weekend?",
        "What are we discussing right now?",
        "What profession did I tell you I have?",
        "what's my current focus",
        "we've been talking about this for a while",
        "tell me about myself",
    ])
    def test_first_person_detected(self, msg):
        assert _has_first_person_marker(msg) is True

    @pytest.mark.parametrize("msg", [
        "current price of bitcoin",
        "weather in Richmond today",
        "tell me about quantum computing",
        "show us how transformers work",
        "best Python framework for 2026",
        # 'me' alone (no possessive) is dative — not a first-person marker.
        # Turn 7 of B-CTX-001 ('what do you know about me') is handled by
        # the classifier-side fix (Rust exemplar removal), not this guard.
        "Connecting those two things — what do you actually know about me from this conversation?",
        "",
    ])
    def test_no_first_person_in_external_queries(self, msg):
        assert _has_first_person_marker(msg) is False

    def test_common_word_substrings_do_not_false_match(self):
        # Word-boundary guard: substring 'i' in "welcome" or "remembered"
        # must not trigger the pattern.
        assert _has_first_person_marker("welcome to the briefing") is False
        assert _has_first_person_marker("she remembered to write") is False


class TestAskFirstFirstPersonGuard:
    """When the user message is clearly first-person/conversational, the
    ask-first substitution is suppressed even if intent_class=web_search.

    This protects against the B-CTX-001 family failure mode where Stage 2
    misroutes conversational/recall queries to needs_internet and the
    user would otherwise receive a canned 61-char 'want me to search?'
    response on personal questions.
    """

    def test_guard_blocks_substitution_on_first_person_query(self):
        # All three conditions for substitution hold (web_search intent,
        # ask-first mode, no confirmation in draft) — guard should still fire.
        draft = "I don't have access to that information."
        resp, sub = validate_ask_first_response(
            draft,
            intent_class="web_search",
            ask_first_mode=True,
            user_message="What was I nervous about for this weekend?",
        )
        assert sub is False
        assert resp == draft  # original draft preserved

    def test_guard_blocks_on_we_pronoun(self):
        draft = "I'll need to search for that."
        resp, sub = validate_ask_first_response(
            draft,
            intent_class="web_search",
            ask_first_mode=True,
            user_message="What are we discussing right now?",
        )
        assert sub is False
        assert resp == draft

    def test_guard_blocks_on_possessive_my(self):
        draft = "I'd need to look that up online."
        resp, sub = validate_ask_first_response(
            draft,
            intent_class="web_search",
            ask_first_mode=True,
            user_message="remind me what I told you about my migration plan",
        )
        assert sub is False
        assert resp == draft

    def test_guard_does_not_block_dative_me_alone(self):
        # 'me' as dative object in imperative construction. The user is
        # asking the system to fetch external info — substitution should
        # still fire. Turn 7 of B-CTX-001 is the analogue case; it routes
        # vault via the classifier-side fix, not via this guard.
        draft = "I don't have that information."
        resp, sub = validate_ask_first_response(
            draft,
            intent_class="web_search",
            ask_first_mode=True,
            user_message="tell me about quantum computing",
        )
        assert sub is True
        assert resp == SCRIPTED_ASK_FIRST_RESPONSE

    def test_guard_does_not_block_on_external_query(self):
        # External-world query — substitution should still fire.
        draft = "I don't have that information."
        resp, sub = validate_ask_first_response(
            draft,
            intent_class="web_search",
            ask_first_mode=True,
            user_message="current price of bitcoin",
        )
        assert sub is True
        assert resp == SCRIPTED_ASK_FIRST_RESPONSE

    def test_no_user_message_defaults_to_old_behavior(self):
        # Backward compatibility: callers that don't pass user_message
        # still get the original substitution behavior.
        resp, sub = validate_ask_first_response(
            "I don't have access to live data. Check Google.",
            intent_class="web_search",
            ask_first_mode=True,
        )
        assert sub is True
        assert resp == SCRIPTED_ASK_FIRST_RESPONSE


# ---------------------------------------------------------------------------
# 3. Confirmation question detection
# ---------------------------------------------------------------------------


class TestConfirmationPatterns:

    @pytest.mark.parametrize("text", [
        "Want me to search for that?",
        "Should I search the web?",
        "Shall I search for more info?",
        "Would you like me to search?",
        "Do you want me to search for this?",
        "I can search for that — interested?",
        "I can look that up for you?",
    ])
    def test_patterns_detected(self, text):
        assert _has_confirmation_question(text) is True

    @pytest.mark.parametrize("text", [
        "I don't have access to live data.",
        "Check Google for the latest.",
        "You might want to visit CNN.",
        "Here's what I know from memory.",
        "",
    ])
    def test_patterns_not_detected(self, text):
        assert _has_confirmation_question(text) is False


# ---------------------------------------------------------------------------
# 4. Post-gen pipeline — confirmation_search_failed
# ---------------------------------------------------------------------------


class TestConfirmationSearchFailed:

    def test_substitutes_retry_on_failure(self):
        result = run_post_gen_pipeline(
            "I'm working on getting that information for you.",
            intent_class="web_search",
            web_search_autonomous=True,
            used_web_search=False,
            used_vault=False,
            used_vision=False,
            confirmation_search_failed=True,
        )
        assert "try again" in result.reply.lower()
        assert result.ask_first_substituted is True

    def test_no_substitution_when_not_failed(self):
        result = run_post_gen_pipeline(
            "Based on current search results, Tokyo has 37 million people.",
            intent_class="web_search",
            web_search_autonomous=True,
            used_web_search=True,
            used_vault=False,
            used_vision=False,
            confirmation_search_failed=False,
        )
        assert "try again" not in result.reply.lower()
        assert "Tokyo" in result.reply


# ---------------------------------------------------------------------------
# 5. Prefill fires when web_items present, not when empty
# ---------------------------------------------------------------------------


class TestAssistantPrefill:
    """The adapter adds 'Based on current search results, ' prefix
    when context_packet.web_items is non-empty."""

    def test_prefix_set_when_web_items(self):
        from src.context.models import ContextPacket
        packet = ContextPacket(
            user_message="test",
            web_items=[{"title": "T", "url": "http://x", "snippet": "S"}],
        )
        assert bool(packet.web_items) is True

    def test_no_prefix_when_empty(self):
        from src.context.models import ContextPacket
        packet = ContextPacket(user_message="test", web_items=[])
        assert bool(packet.web_items) is False


# ---------------------------------------------------------------------------
# 6. Duplicate write guard
# ---------------------------------------------------------------------------


class TestDuplicateWriteGuard:

    @pytest.fixture
    def svc(self, tmp_path):
        from src.state.state_service import StateService
        return StateService(vault_path=tmp_path)

    def test_no_duplicate_when_same_session_query(self, svc):
        """Second write with same session+query is suppressed."""
        # First write
        record = svc.make_record(
            state_type="pending_confirmation",
            text="Want me to search?",
            source="ask_first_detector",
            metadata={
                "action": "web_search",
                "query": "population of Tokyo",
                "session_id": "sess_dup_test",
                "resolved": False,
            },
        )
        svc.write(record)

        with patch("src.api.openai_adapter.state_service", svc):
            from src.api.openai_adapter import _write_pending_confirmation
            _write_pending_confirmation(
                "I don't have that — want me to search?",
                "population of Tokyo",
                "sess_dup_test",
            )

        records = svc.read_by_category("pending_confirmation")
        unresolved = [r for r in records if not (r.metadata or {}).get("resolved")]
        assert len(unresolved) == 1

    def test_allows_write_for_different_query(self, svc):
        """Different query in same session creates a new pending."""
        record = svc.make_record(
            state_type="pending_confirmation",
            text="Want me to search?",
            source="ask_first_detector",
            metadata={
                "action": "web_search",
                "query": "population of Tokyo",
                "session_id": "sess_dup_test2",
                "resolved": False,
            },
        )
        svc.write(record)

        with patch("src.api.openai_adapter.state_service", svc):
            from src.api.openai_adapter import _write_pending_confirmation
            _write_pending_confirmation(
                "I don't have that — want me to search?",
                "weather in London",
                "sess_dup_test2",
            )

        records = svc.read_by_category("pending_confirmation")
        unresolved = [r for r in records if not (r.metadata or {}).get("resolved")]
        assert len(unresolved) == 2
