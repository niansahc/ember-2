"""
tests/test_ask_first_offer_phrasing.py

Only Ember's code makes a search offer (ADR-048). On an ask-first exchange the
reply is always the fixed offer and a pending is written for the user's text.
A model offer on any other exchange writes nothing, whatever its wording, so a
"yes" to it can never send the user's text to the search engines. A
constitutional refusal is never turned into an offer, and a first-person
exchange (the query looks personal) is not an ask-first exchange.

OFFER_PATTERNS no longer decides pendings. It only detects the model's own
offers (has_search_offer).

End-to-end cases run through /v1/chat/completions with the helpers from
tests/test_ask_first_routing.py against the conftest throwaway test vault.
Synthetic session ids only.
"""

from __future__ import annotations

import pytest

from src.context.policies import ContextPolicy
from src.llm.ask_first_validator import (
    REPEAT_SEARCH_OFFER,
    RETRY_OFFER_RESPONSE,
    SCRIPTED_ASK_FIRST_RESPONSE,
    find_offer_sentence,
    has_search_offer,
)
from src.llm.post_gen_pipeline import run_post_gen_pipeline
from tests.conftest import active_pendings as _active_pendings
from tests.test_ask_first_routing import (  # noqa: F401  (client is a fixture)
    CLEAN_HIT,
    MODEL_REPLY,
    OFFER_REPLY,
    _adapter,
    _searxng,
    _turn,
    client,
)

QUERY = "latest widget price"
DEFAULT = ContextPolicy(name="default")


def _model_message(llm) -> str:
    return llm.generate_response.call_args.args[0].user_message


# --- Pendings come only from the code's offers ---------------------------------

def test_model_offer_without_a_code_offer_writes_no_pending(client, isolate_to_test_vault):
    """The model offers on an exchange the classifier did not route to web
    search: nothing is pending, and the "yes" runs as a new request."""
    sid = "sess_test_model_offer_no_pending"
    _turn(client, isolate_to_test_vault, [], query=QUERY, session_id=sid,
          request_policy=DEFAULT, reply="I don't have that. Want me to look it up online?")
    assert _active_pendings(sid) == []
    _, _, llm, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                           query="yes", session_id=sid, request_policy=DEFAULT)
    assert get.call_count == 0
    assert _model_message(llm) == "yes"


def test_ask_first_exchange_writes_a_pending_with_the_fixed_offer(client, isolate_to_test_vault):
    """Positive control for the test above: the code's offer is pending."""
    sid = "sess_test_ask_first_pending"
    _, text, _, _ = _turn(client, isolate_to_test_vault, [], query=QUERY, session_id=sid)
    assert text == SCRIPTED_ASK_FIRST_RESPONSE
    pendings = _active_pendings(sid)
    assert [p.metadata["query"] for p in pendings] == [QUERY]
    assert pendings[0].text == SCRIPTED_ASK_FIRST_RESPONSE


def test_ask_first_reply_is_always_the_fixed_offer(client, isolate_to_test_vault):
    """The model's own offer wording never reaches the user on an ask-first
    exchange: the consent text is always the fixed sentence naming the web."""
    sid = "sess_test_ask_first_fixed_text"
    _, text, _, _ = _turn(client, isolate_to_test_vault, [], query=QUERY, session_id=sid,
                          reply="Shall I look that up?")
    assert text == SCRIPTED_ASK_FIRST_RESPONSE
    assert [p.text for p in _active_pendings(sid)] == [SCRIPTED_ASK_FIRST_RESPONSE]


def test_refusal_on_an_ask_first_exchange_is_kept_and_offers_nothing(
        client, isolate_to_test_vault):
    """A constitutional refusal is never replaced by an offer (CLAUDE.md
    implementation standard 2). The control is the fixed-offer test above."""
    sid = "sess_test_ask_first_refusal"
    refusal = "I can't help with that."
    _, text, _, _ = _turn(client, isolate_to_test_vault, [], query=QUERY, session_id=sid,
                          reply=refusal)
    assert text == refusal
    assert _active_pendings(sid) == []


def test_first_person_exchange_is_not_ask_first(client, isolate_to_test_vault):
    """A query that looks personal is answered as a normal exchange: no ask
    block in the prompt, no offer, nothing pending."""
    sid = "sess_test_first_person_no_offer"
    _, text, llm, _ = _turn(client, isolate_to_test_vault, [],
                            query="what did I say about the widget price", session_id=sid)
    assert llm.generate_response.call_args.kwargs["ask_first_active"] is False
    assert text == MODEL_REPLY
    assert _active_pendings(sid) == []


def test_external_query_is_ask_first(client, isolate_to_test_vault):
    """Control for the test above: the same intent without a first-person
    marker is an ask-first exchange."""
    sid = "sess_test_external_ask_first"
    _, _, llm, _ = _turn(client, isolate_to_test_vault, [], query=QUERY, session_id=sid)
    assert llm.generate_response.call_args.kwargs["ask_first_active"] is True


def test_stateless_holds_only_the_code_offer(client, isolate_to_test_vault):
    """Vault off: the ask-first offer is held in process; a model offer on a
    default exchange is not."""
    store = _adapter()._stateless_pending_store
    _turn(client, isolate_to_test_vault, [], query=QUERY, session_id="sess_test_sl_code",
          vault_enabled=False)
    _turn(client, isolate_to_test_vault, [], query=QUERY, session_id="sess_test_sl_model",
          vault_enabled=False, request_policy=DEFAULT, reply=OFFER_REPLY)
    assert store.take("sess_test_sl_code") == {"action": "web_search", "query": QUERY}
    assert store.take("sess_test_sl_model") is None


# --- Repeat-search offers after a search failure a repeat could fix ------------

BLOCKED = _searxng([], unresponsive=["brave"])


def test_failed_autonomous_search_offers_a_repeat(client, isolate_to_test_vault):
    """Engines blocked: the reply ends with the code's repeat-search offer,
    the pending holds the text the search already sent, and "yes" repeats it."""
    sid = "sess_test_repeat_autonomous"
    _, text, _, get = _turn(client, isolate_to_test_vault, [BLOCKED], query=QUERY,
                            session_id=sid, autonomous=True)
    assert get.call_count == 1
    assert text.endswith(REPEAT_SEARCH_OFFER)
    assert [(p.metadata["query"], p.text) for p in _active_pendings(sid)] == [
        (QUERY, REPEAT_SEARCH_OFFER)]
    _, _, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                         query="yes", session_id=sid, autonomous=True)
    assert [call.kwargs["params"]["q"] for call in get.call_args_list] == [QUERY]


def test_search_that_found_nothing_offers_no_repeat(client, isolate_to_test_vault):
    """no_results is a no-repeat reason: a repeat finds nothing again. The
    positive control is the engines-blocked test above."""
    sid = "sess_test_repeat_no_results"
    _, text, _, get = _turn(client, isolate_to_test_vault, [_searxng([])], query=QUERY,
                            session_id=sid, autonomous=True)
    assert get.call_count == 1
    assert REPEAT_SEARCH_OFFER not in text
    assert _active_pendings(sid) == []


def test_failed_explicit_search_offers_a_repeat(client, isolate_to_test_vault):
    sid = "sess_test_repeat_explicit"
    query = "search the web for the latest widget price"
    _, text, _, _ = _turn(client, isolate_to_test_vault, [BLOCKED], query=query,
                          session_id=sid, real_classifier=True)
    assert text.endswith(REPEAT_SEARCH_OFFER)
    assert [p.metadata["query"] for p in _active_pendings(sid)] == [query]


def test_failed_clarified_search_offers_a_repeat(client, isolate_to_test_vault):
    """The search content after a search clarification ("google please")."""
    sid = "sess_test_repeat_b2"
    _, text, _, _ = _turn(client, isolate_to_test_vault, [BLOCKED], query="the eagles game",
                          session_id=sid, awaiting_search=True)
    assert text.endswith(REPEAT_SEARCH_OFFER)
    assert [p.metadata["query"] for p in _active_pendings(sid)] == ["the eagles game"]


def test_failed_stateless_search_holds_the_repeat_offer(client, isolate_to_test_vault):
    store = _adapter()._stateless_pending_store
    _, text, _, _ = _turn(client, isolate_to_test_vault, [BLOCKED], query=QUERY,
                          session_id="sess_test_repeat_stateless", autonomous=True,
                          vault_enabled=False)
    assert text.endswith(REPEAT_SEARCH_OFFER)
    assert store.take("sess_test_repeat_stateless") == {"action": "web_search", "query": QUERY}


@pytest.mark.parametrize("status,reason,offered", [
    ("unavailable", "engines_blocked", True),
    ("unavailable", "timeout", True),
    ("unavailable", "request_error", True),
    ("unavailable", "low_relevance", False),
    ("unavailable", "filtered", False),
    ("empty", "no_results", False),
])
def test_repeat_offer_follows_the_failure_reason(status, reason, offered):
    result = run_post_gen_pipeline(
        MODEL_REPLY, intent_class="web_search", web_search_autonomous=True,
        used_web_search=False, used_vault=False, used_vision=False,
        web_search_status=status, web_search_reason=reason,
    )
    assert result.reply.endswith(REPEAT_SEARCH_OFFER) is offered
    assert (result.code_offer is not None) is offered


def test_refusal_after_a_failed_search_gets_no_repeat_offer():
    refusal = "I can't help with that."
    result = run_post_gen_pipeline(
        refusal, intent_class="web_search", web_search_autonomous=True,
        used_web_search=False, used_vault=False, used_vision=False,
        web_search_status="unavailable", web_search_reason="engines_blocked",
    )
    assert result.reply == refusal
    assert result.code_offer is None


# --- Detecting the model's own offers --------------------------------------------

OFFERS = [
    "I don't have that. Want me to search for it?",
    "Do you want me to look it up?",
    "Want me to look up the score online?",
    "Want me to try the search again?",
    "Let me know if you want me to search.",
    "Should I search the web?",
    "Shall I look that up?",
    "Would you like me to look online?",
    "Would you like me to find out the price on the web?",
    "I can search for that, interested?",
    "I could look it up for you if you like.",
    # Curly apostrophe, as a mobile keyboard sends it.
    "I could search for that if you\u2019d like.",
]

NON_OFFERS = [
    "I don't have access to live data.",
    "Check Google for the latest.",
    "I can search but I won't.",
    "Here's what I know from memory.",
    "Would you like me to look over the draft for typos?",
    "Want me to find the file?",
    "Should I look at your notes?",
    "Do you want me to look into it?",
    "Want me to try again?",
]


@pytest.mark.parametrize("offer", OFFERS)
def test_model_offers_are_detected(offer):
    assert has_search_offer(offer) is True


@pytest.mark.parametrize("text", NON_OFFERS)
def test_non_offers_are_not_detected(text):
    """Control for the test above: the patterns are not a match-all."""
    assert has_search_offer(text) is False


def test_curly_offer_sentence_is_returned_as_written():
    reply = "I don't have it. I could search for that if you\u2019d like."
    assert find_offer_sentence(reply) == "I could search for that if you\u2019d like."


def test_offer_sentence_is_the_sentence_with_the_offer():
    reply = "Prices change daily. I don't have today's. Want me to search for it? Happy to."
    assert find_offer_sentence(reply) == "Want me to search for it?"
    assert find_offer_sentence("No offer here.") is None


def test_post_gen_retry_offer_is_the_shared_constant():
    from src.llm.post_gen_pipeline import run_post_gen_pipeline
    result = run_post_gen_pipeline(
        "I'm working on it.", intent_class="web_search",
        web_search_autonomous=False, used_web_search=False, used_vault=False,
        used_vision=False, confirmation_search_failed=True,
    )
    assert result.reply == RETRY_OFFER_RESPONSE
