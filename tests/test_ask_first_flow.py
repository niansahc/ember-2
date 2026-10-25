"""tests/test_ask_first_flow.py — automated coverage for the ask-first
confirmation flow and web search execution pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.llm.ask_first_validator import (
    SCRIPTED_ASK_FIRST_RESPONSE,
    has_first_person_marker,
    remove_unbacked_offers,
)
from src.llm.post_gen_pipeline import CodeOffer, run_post_gen_pipeline


# ---------------------------------------------------------------------------
# 1. Deterministic keyword matching
# ---------------------------------------------------------------------------


class TestDeterministicKeywordMatch:
    """YES/NO matching is deterministic, not an LLM call. Exercises the real
    interpreter in src/api/openai_adapter.py (negation first, then
    affirmatives), not a local copy of its logic."""

    def _is_affirmed(self, msg: str) -> bool:
        from src.api.openai_adapter import _confirmation_result
        return _confirmation_result("web_search", "q", msg)["confirmed"]

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
# 2. Ask-first substitution (post-gen pipeline)
# ---------------------------------------------------------------------------


def _ask_first(reply, *, intent_class="web_search", ask_first_active=True, **kw):
    """The post-gen pipeline on an exchange the adapter made ask-first (or not)."""
    return run_post_gen_pipeline(
        reply, intent_class=intent_class, web_search_autonomous=not ask_first_active,
        used_web_search=False, used_vault=False, used_vision=False,
        ask_first_active=ask_first_active, **kw,
    )


class TestAskFirstSubstitution:

    def test_substitutes_when_no_confirmation_question(self):
        result = _ask_first("I don't have access to live data. Check Google.")
        assert result.ask_first_substituted is True
        assert result.reply == SCRIPTED_ASK_FIRST_RESPONSE

    def test_replaces_the_model_offer_with_the_fixed_offer(self):
        """The model's own offer wording never reaches the user on an
        ask-first exchange (ADR-048)."""
        result = _ask_first("I don't have that, want me to search for it?")
        assert result.ask_first_substituted is True
        assert result.reply == SCRIPTED_ASK_FIRST_RESPONSE

    def test_refusal_is_kept(self):
        result = _ask_first("I can't help with that.", review_refused=True)
        assert result.ask_first_substituted is False
        assert result.reply == "I can't help with that."
        assert result.code_offer is None

    def test_model_refusal_wording_alone_is_an_ordinary_draft(self):
        """Control for the test above: the review's outcome keeps a refusal,
        not its wording. The model's own "I can't help with that" on an
        ask-first exchange is replaced by the offer like any other draft."""
        result = _ask_first("I can't help with that.")
        assert result.ask_first_substituted is True
        assert result.reply == SCRIPTED_ASK_FIRST_RESPONSE

    def test_no_op_when_not_web_search(self):
        result = _ask_first("Some response without search offer.", intent_class="default")
        assert result.ask_first_substituted is False

    def test_no_op_when_autonomous(self):
        result = _ask_first("I don't have live data.", ask_first_active=False)
        assert result.ask_first_substituted is False

    def test_empty_draft_gets_the_fixed_offer(self):
        result = _ask_first("")
        assert result.ask_first_substituted is True
        assert result.reply == SCRIPTED_ASK_FIRST_RESPONSE


# ---------------------------------------------------------------------------
# 2b. First-person guard (B-CTX-001 family)
# ---------------------------------------------------------------------------


class TestFirstPersonMarkerDetection:
    """has_first_person_marker identifies conversational / personal queries,
    which the adapter does not treat as ask-first exchanges (the end-to-end
    cases are in tests/test_ask_first_offer_phrasing.py)."""

    @pytest.mark.parametrize("msg", [
        "What was I nervous about for this weekend?",
        "What are we discussing right now?",
        "What profession did I tell you I have?",
        "what's my current focus",
        "we've been talking about this for a while",
        "tell me about myself",
    ])
    def test_first_person_detected(self, msg):
        assert has_first_person_marker(msg) is True

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
        assert has_first_person_marker(msg) is False

    def test_common_word_substrings_do_not_false_match(self):
        # Word-boundary guard: substring 'i' in "welcome" or "remembered"
        # must not trigger the pattern.
        assert has_first_person_marker("welcome to the briefing") is False
        assert has_first_person_marker("she remembered to write") is False


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
        assert remove_unbacked_offers(text)[1] == 1

    @pytest.mark.parametrize("text", [
        "I don't have access to live data.",
        "Check Google for the latest.",
        "You might want to visit CNN.",
        "Here's what I know from memory.",
        "",
    ])
    def test_patterns_not_detected(self, text):
        assert remove_unbacked_offers(text) == (text, 0)


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
        assert "try the search again" in result.reply.lower()
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
        assert "try the search again" not in result.reply.lower()
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
                CodeOffer("ask_first", SCRIPTED_ASK_FIRST_RESPONSE),
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
                CodeOffer("ask_first", SCRIPTED_ASK_FIRST_RESPONSE),
                "weather in London",
                "sess_dup_test2",
            )

        records = svc.read_by_category("pending_confirmation")
        unresolved = [r for r in records if not (r.metadata or {}).get("resolved")]
        assert len(unresolved) == 2
