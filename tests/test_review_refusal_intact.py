"""
tests/test_review_refusal_intact.py

A constitutional refusal reaches the user as the review wrote it (CLAUDE.md
implementation standard 2, ADR-048, lean review of PR #302 finding 1).

LLMAdapter returns a refuse_redirect refusal as RefusalText; the chat path
reads the type once and passes the outcome on as a flag. On a refusal: no
search offer and no pending, no substituted text, no grounding revision, no
coaching filter. The refusal is never recognised by its wording: a real one
is the review model's reason and safer alternative, or the heuristic
fallback, and neither matched the phrase list this replaced.

Both refusal forms are covered, each with a control that sends the same text
without the review's outcome, so a broken flag cannot pass. Synthetic text
only; the chat tests use the _turn harness of tests/test_ask_first_routing.py
and the conftest throwaway test vault.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.llm.ask_first_validator import (
    REPEAT_SEARCH_OFFER,
    RETRY_OFFER_RESPONSE,
    SCRIPTED_ASK_FIRST_RESPONSE,
)
from src.llm.post_gen_pipeline import (
    WEB_SEARCH_UNAVAILABLE_DISCLOSURE,
    CodeOffer,
    run_post_gen_pipeline,
)
from src.llm.refusal import RefusalText, is_review_refusal
from src.safety.models import RefusalRedirect
from src.safety.review_service import ResponseReviewService
from tests.conftest import active_pendings
from tests.test_ask_first_routing import (  # noqa: F401  (client is a fixture)
    QUERY,
    _turn,
    client,
)

# The two forms a refuse_redirect refusal takes (src/safety/review_service.py):
# the review model's reason and safer alternative, and the heuristic fallback
# used when the model's refusal cannot be parsed. The model form names a
# place to look, which the web-refusal validator's patterns match.
MODEL_REFUSAL = RefusalRedirect(
    reason="That request would help someone get hurt.",
    safer_alternative="You could check a safety resource instead.",
).as_text()
HEURISTIC_REFUSAL = ResponseReviewService(llm_callable=None)._heuristic_refusal().as_text()
REFUSALS = pytest.mark.parametrize(
    "refusal", [MODEL_REFUSAL, HEURISTIC_REFUSAL], ids=["model", "heuristic"])

WEB_ITEMS = [{"title": "Result", "url": "https://example.com/a",
              "snippet": "A synthetic search snippet."}]


def _postgen(reply, *, review_refused, **kw):
    kw.setdefault("intent_class", "web_search")
    return run_post_gen_pipeline(
        reply, web_search_autonomous=False, used_web_search=False,
        used_vault=False, used_vision=False, review_refused=review_refused, **kw)


# --- The type carries the outcome -------------------------------------------------

def test_refusal_text_is_the_text_and_carries_the_outcome():
    refusal = RefusalText(MODEL_REFUSAL)
    assert refusal == MODEL_REFUSAL
    assert is_review_refusal(refusal) is True
    # Control: the same words as a plain str are not the review's outcome.
    assert is_review_refusal(MODEL_REFUSAL) is False


# --- The post-gen pipeline keeps a refusal as written ------------------------------

@REFUSALS
def test_ask_first_exchange_keeps_the_refusal_and_offers_nothing(refusal):
    result = _postgen(refusal, review_refused=True, ask_first_active=True)
    assert result.reply == refusal
    assert result.code_offer is None
    assert result.ask_first_substituted is False


@REFUSALS
def test_same_text_without_the_outcome_gets_the_offer(refusal):
    """Control for the test above: what keeps a refusal is the review's
    outcome, not its wording. The old phrase list matched neither form, so
    this is what a refused ask-first exchange used to get."""
    result = _postgen(refusal, review_refused=False, ask_first_active=True)
    assert result.reply == SCRIPTED_ASK_FIRST_RESPONSE
    assert result.code_offer == CodeOffer("ask_first", SCRIPTED_ASK_FIRST_RESPONSE)


@REFUSALS
def test_failed_confirm_search_keeps_the_refusal(refusal):
    result = _postgen(refusal, review_refused=True, confirmation_search_failed=True)
    assert result.reply == refusal
    assert result.code_offer is None
    # Control: without the outcome the retry offer replaces it.
    assert _postgen(refusal, review_refused=False,
                    confirmation_search_failed=True).reply == RETRY_OFFER_RESPONSE


@REFUSALS
def test_unavailable_search_adds_no_disclosure_and_no_repeat_offer(refusal):
    kw = dict(web_search_status="unavailable", web_search_reason="engines_blocked")
    result = _postgen(refusal, review_refused=True, **kw)
    assert result.reply == refusal
    assert result.code_offer is None
    control = _postgen(refusal, review_refused=False, **kw)
    assert control.reply.startswith(WEB_SEARCH_UNAVAILABLE_DISCLOSURE)
    assert control.reply.endswith(REPEAT_SEARCH_OFFER)


def test_web_results_never_replace_a_refusal():
    """The model refusal names a place to look ("You could check ..."), which
    the web-refusal validator reads as an RLHF refusal and would replace with
    search snippets."""
    result = _postgen(MODEL_REFUSAL, review_refused=True, web_items=WEB_ITEMS)
    assert result.reply == MODEL_REFUSAL
    assert result.web_refusal_substituted is False
    control = _postgen(MODEL_REFUSAL, review_refused=False, web_items=WEB_ITEMS)
    assert control.web_refusal_substituted is True


# --- End to end through /v1/chat/completions --------------------------------------

@REFUSALS
@pytest.mark.parametrize("stream", [False, True])
def test_refused_ask_first_exchange_reaches_the_user_intact(
        client, isolate_to_test_vault, refusal, stream):
    """Ask-first (autonomous off, web_search intent): the refusal is sent as
    written, no pending is written, and neither the coaching filter nor (on
    the stream) the grounding revision touches it."""
    sid = f"sess_test_refusal_intact_{stream}_{abs(hash(refusal)) % 10000}"
    coaching = MagicMock(side_effect=lambda reply, *a, **kw: "COACHED")
    revision = AsyncMock(return_value="REVISED")
    with patch("src.safety.grounding_check.run_revision_pass", new=revision):
        _, text, _, get = _turn(client, isolate_to_test_vault, [], query=QUERY,
                                session_id=sid, stream=stream,
                                reply=RefusalText(refusal), coaching=coaching,
                                grounding=(False, "unsupported"))
    assert text == refusal
    assert active_pendings(sid) == []
    assert get.call_count == 0
    coaching.assert_not_called()
    revision.assert_not_called()


@REFUSALS
@pytest.mark.parametrize("stream", [False, True])
def test_same_text_from_the_model_gets_the_offer_and_a_pending(
        client, isolate_to_test_vault, refusal, stream):
    """Control for the test above: the same words generated as a plain draft
    (no review outcome) are replaced by the offer, and its pending is
    written. The coaching filter runs, and on the stream the grounding
    revision does too."""
    sid = f"sess_test_refusal_control_{stream}_{abs(hash(refusal)) % 10000}"
    coaching = MagicMock(side_effect=lambda reply, *a, **kw: reply)
    revision = AsyncMock(side_effect=lambda reply, *a, **kw: reply)
    with patch("src.safety.grounding_check.run_revision_pass", new=revision):
        _, text, _, _ = _turn(client, isolate_to_test_vault, [], query=QUERY,
                              session_id=sid, stream=stream, reply=str(refusal),
                              coaching=coaching, grounding=(False, "unsupported"))
    assert text == SCRIPTED_ASK_FIRST_RESPONSE
    assert [p.metadata["query"] for p in active_pendings(sid)] == [QUERY]
    coaching.assert_called_once()
    assert revision.await_count == (1 if stream else 0)
