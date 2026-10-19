"""
tests/test_ask_first_routing.py

Regression tests for ask-first routing. Each test asserts the CORRECT
behaviour.

  - Declines win: any negation word declines, checked before affirmatives,
    with one fixed carve-out ("why not", "no problem", "not a problem").
  - "yes, but what about X?" keeps X; an elaborated yes ("Yes, that would be
    great") carries no follow-up.
  - A non-web offer ("look over the draft") leaves nothing a "yes" could
    turn into a web search.
  - A failed confirm search's retry offer leaves a pending; "yes" re-runs.
  - Stateless mode (vault toggle off): explicit requests search; ask-first
    pendings live in process with a TTL, never in the vault.
  - B2 next-turn dispatch searches under ask-first.
  - The "searching" status frame fires only on turns that search.

Mocks follow tests/test_search_quarantine_paths.py: SearXNG via
src.tools.web_search.requests.get, the model via llm_adapter. The pending
checker and writer are REAL and run against the conftest throwaway test
vault. Synthetic session ids only.
"""

from __future__ import annotations

import inspect
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import src.tools.web_search as web_search_mod
from src.llm.ask_first_validator import RETRY_OFFER_RESPONSE, SCRIPTED_ASK_FIRST_RESPONSE
from src.tools.web_search import WebSearchResult
from tests.conftest import active_pendings as _active_pendings
from tests.conftest import seed_pending, sse_events

MODEL_REPLY = "Here is an answer."
OFFER_REPLY = "I don't have that. Want me to search?"
QUERY = "latest bitcoin price"
CLEAN_HIT = {"title": "Bitcoin price", "url": "https://example.com/btc",
             "content": "bitcoin price today"}


# --- helpers -------------------------------------------------------------------

@pytest.fixture
def client(keyless_client):
    return keyless_client


def _adapter():
    import src.api.openai_adapter as adapter
    return adapter


def _searxng(items, unresponsive=()):
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "results": list(items),
        "unresponsive_engines": [[name, "timeout"] for name in unresponsive],
    }
    response.raise_for_status.return_value = None
    return response


def _seed_pending(session_id, query=QUERY):
    return seed_pending(session_id, query)


def _turn(client, test_vault, responses, *, query, session_id, stream=False,
          autonomous=False, vault_enabled=True, real_classifier=False,
          assembly_policy=None, request_policy=None, awaiting_search=False,
          reply=MODEL_REPLY, fail=None, record_reply_error=None, image=False):
    """One /v1/chat/completions turn. _check_pending_confirmation and
    _write_pending_confirmation are REAL and run against the test vault.
    request_policy replaces the request classifier's web_search verdict.
    fail is raised by generation; record_reply_error by the reply write;
    image sends an image whose vision preprocessing fails. A failing turn
    returns resp None (a raise reached the test client) or the error stream,
    and text "".
    Returns (resp, text, llm mock, requests.get mock)."""
    adapter = _adapter()
    from src.context.policies import ContextPolicy, _web_search_policy

    if assembly_policy is None:
        assembly_policy = ContextPolicy(name="default")
    with ExitStack() as stack:
        get = stack.enter_context(
            patch.object(web_search_mod.requests, "get", side_effect=responses))
        stack.enter_context(patch.object(
            web_search_mod, "get_web_search_min_term_hits", return_value=0))
        stack.enter_context(patch("src.context.service.classify_query",
                                  return_value=assembly_policy))
        if not real_classifier:
            stack.enter_context(patch("src.context.policies.classify_query",
                                      return_value=request_policy
                                      or _web_search_policy(explicit=False)))
        stack.enter_context(patch.object(adapter.context_service.retriever, "retrieve",
                                         return_value=([], [], [], [], None)))
        llm = stack.enter_context(patch("src.api.openai_adapter.llm_adapter"))
        # The recorder is mocked, but its .vault must be a real path (the
        # throwaway test vault): vault_binding would otherwise bind
        # str(MagicMock).
        rec = stack.enter_context(patch("src.api.openai_adapter.ExchangeRecorder"))
        rec.return_value.vault = str(test_vault)
        rec.return_value.session_id = session_id
        if record_reply_error is not None:
            rec.return_value.record_reply.side_effect = record_reply_error
        if image:
            vision = stack.enter_context(patch("src.api.openai_adapter.vision_service"))
            vision.analyze.return_value = None
        for name in ("_background_state_extraction", "_detect_and_write_commitment",
                     "_detect_task_in_response", "_background_topic_decline_resolution",
                     "_spawn_deviation_detection", "_spawn_post_exchange_extractors"):
            stack.enter_context(patch(f"src.api.openai_adapter.{name}"))
        onb = stack.enter_context(patch("src.api.openai_adapter.onboarding_service"))
        onb.is_active.return_value = False
        stack.enter_context(patch("src.core.preferences.get",
                                  side_effect=lambda k, d=None, *a, **kw:
                                  autonomous if k == "web_search_autonomous" else d))
        stack.enter_context(patch(
            "src.api.openai_adapter._prior_turn_awaits_search_content",
            return_value=awaiting_search))
        stack.enter_context(patch("src.llm.coaching_filter.filter_coaching_frame",
                                  side_effect=lambda reply, *a, **kw: reply))
        stack.enter_context(patch("src.safety.grounding_check.should_short_circuit_grounding",
                                  return_value=False))
        stack.enter_context(patch("src.safety.grounding_check.run_grounding_check",
                                  new=AsyncMock(return_value=(True, None))))
        llm.generate_response.return_value = reply
        llm.generate_response_iter.return_value = iter([reply])
        if fail is not None:
            llm.generate_response.side_effect = fail
            llm.generate_response_iter.side_effect = fail

        content = query
        if image:
            content = [{"type": "text", "text": query},
                       {"type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AAAA"}}]
        try:
            resp = client.post(
                "/v1/chat/completions",
                json={"model": "ember", "stream": stream, "vault_enabled": vault_enabled,
                      "messages": [{"role": "user", "content": content}]},
                headers={"X-Session-ID": session_id},
            )
        except Exception:
            if fail is None and record_reply_error is None:
                raise
            resp = None
    if fail is not None or record_reply_error is not None or image:
        return resp, "", llm, get
    assert resp.status_code == 200
    if stream:
        text = "".join(
            e["choices"][0]["delta"].get("content") or ""
            for e in sse_events(resp.text)
            if isinstance(e, dict) and e.get("choices")
        )
    else:
        text = resp.json()["choices"][0]["message"]["content"]
    return resp, text, llm, get


def _searched_query(get) -> str:
    return get.call_args.kwargs["params"]["q"]


# --- 1. Declines win -------------------------------------------------------------

DECLINES = [
    "No, please don't search",
    "Not okay, skip the search",
    "Please no",
    "not okay, skip it",
    "no thanks, just search my notes instead",
    "Nope, okay?",
    "stop",
    "never mind",
    "Don\u2019t search, okay",
    "why not search later, no",
    "no",
]
CONFIRMS = [
    "yes", "Yes!", "Please", "Sure, go ahead", "search", "y", "OK.",
    "Sure, why not", "no problem, go ahead", "not a problem, yes",
]


@pytest.mark.parametrize("message", DECLINES)
def test_reply_with_a_negation_declines(message):
    confirmed, follow_up = _adapter()._interpret_confirmation_reply(message)
    assert (confirmed, follow_up) == (False, "")


@pytest.mark.parametrize("message", CONFIRMS)
def test_affirmatives_and_affirming_idioms_confirm(message):
    """Control for the declines above, and the fixed idiom carve-out."""
    confirmed, _ = _adapter()._interpret_confirmation_reply(message)
    assert confirmed is True


@pytest.mark.parametrize("message", ["No, please don't search", "Please no",
                                     "why not search later, no"])
def test_decline_resolves_the_pending_and_runs_no_search(client, isolate_to_test_vault, message):
    """End to end: the decline is a plain turn on the user's own words and
    the offer is resolved. The request classifier is pinned to default: the
    real one routes none of these declines to web_search, so no new offer
    follows."""
    from src.context.policies import ContextPolicy
    sid = f"sess_test_decline_{abs(hash(message)) % 100000}"
    seeded = _seed_pending(sid)
    _, text, llm, get = _turn(client, isolate_to_test_vault, [], query=message,
                              session_id=sid, request_policy=ContextPolicy(name="default"))
    assert get.call_count == 0
    assert llm.generate_response.call_args.args[0].user_message == message
    svc = _adapter().state_service
    assert seeded.id in svc.resolved_ids(svc.read_by_category("pending_confirmation"))
    assert _active_pendings(sid) == []


def test_confirm_runs_the_stored_query(client, isolate_to_test_vault):
    """Control for the decline test above."""
    sid = "sess_test_confirm_ctl"
    _seed_pending(sid)
    resp, text, llm, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                                 query="yes please", session_id=sid)
    assert get.call_count == 1
    assert _searched_query(get) == QUERY
    assert resp.headers.get("x-ember-web-search") == "true"


# --- 2. "yes, but ..." keeps the new content -------------------------------------

def _apply(message, sid):
    from src.api.pregeneration import GenerationContext, GenerationWork
    _seed_pending(sid)
    ctx = GenerationContext(
        session_id=sid, project_id=None, project_name=None, is_test=False,
        vault_enabled=True, skip_vault_read=False, skip_vault_write=False,
        completion_id="chatcmpl-test", stream=False, policy=None, raw_user_message="")
    work = GenerationWork(message=message)
    with patch("src.tools.web_search.run_web_search",
               return_value=WebSearchResult(items=[], status="empty",
                                            reason="no_results")) as rws:
        _adapter()._apply_confirmation(ctx, work)
    return work, rws


def test_yes_with_new_content_keeps_the_new_content():
    work, rws = _apply("yes, but what about the weather tomorrow?", "sess_test_yes_but")
    assert work.confirmation_confirmed is True
    assert rws.call_args.args[0] == QUERY
    assert work.raw_message == QUERY
    assert work.message == f"{QUERY}\n\nwhat about the weather tomorrow?"


@pytest.mark.parametrize("message", ["yes please", "Sure, go ahead",
                                     "yes, search the web for it", "Sure, why not"])
def test_plain_yes_carries_no_follow_up(message):
    """Control: a plain yes becomes exactly the stored query."""
    work, _ = _apply(message, f"sess_test_plain_yes_{abs(hash(message)) % 100000}")
    assert work.confirmation_confirmed is True
    assert work.message == QUERY


@pytest.mark.parametrize("message", [
    "Yes, that would be great", "yes sure thing", "Yeah that's fine",
    "Yes, I'd appreciate it", "ok cool", "Sure, thanks a lot",
])
def test_elaborated_yes_carries_no_follow_up(message):
    """No connector and no question: the words only elaborate the yes."""
    confirmed, follow_up = _adapter()._interpret_confirmation_reply(message)
    assert (confirmed, follow_up) == (True, "")


@pytest.mark.parametrize("message,expected", [
    ("yes, but what about the weather tomorrow?", "what about the weather tomorrow?"),
    ("Yes please, and is it open Sunday", "is it open Sunday"),
    ("sure, also check the forecast", "check the forecast"),
    ("ok then compare it with last year", "compare it with last year"),
    ("yes, is it open on Sunday?", "is it open on Sunday?"),
])
def test_connector_or_question_keeps_the_follow_up(message, expected):
    """Control for the test above: new content after a connector or in a
    question is kept."""
    confirmed, follow_up = _adapter()._interpret_confirmation_reply(message)
    assert (confirmed, follow_up) == (True, expected)


DRAFT = "here is my draft paragraph about a synthetic topic"


def test_non_web_offer_then_yes_never_sends_the_draft_to_searxng(
        client, isolate_to_test_vault):
    """A reply offering to look over the draft is not a search offer: no
    pending is written, and the user's "yes" searches nothing, so the draft
    text never reaches SearXNG."""
    from src.context.policies import ContextPolicy
    sid = "sess_test_non_web_offer"
    _turn(client, isolate_to_test_vault, [], query=DRAFT, session_id=sid,
          request_policy=ContextPolicy(name="default"),
          reply="Would you like me to look over the draft for typos?")
    assert _active_pendings(sid) == []
    _, _, llm, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                           query="yes", session_id=sid,
                           request_policy=ContextPolicy(name="default"))
    assert get.call_count == 0
    assert all(DRAFT not in str(call) for call in get.call_args_list)
    assert llm.generate_response.call_args.args[0].user_message == "yes"


def test_web_offer_then_yes_searches_the_stored_message(client, isolate_to_test_vault):
    """Positive control for the test above: a real search offer, then "yes",
    does send the stored message to SearXNG."""
    from src.context.policies import ContextPolicy
    sid = "sess_test_web_offer_control"
    _turn(client, isolate_to_test_vault, [], query=DRAFT, session_id=sid,
          request_policy=ContextPolicy(name="default"),
          reply="Want me to look that up online?")
    assert len(_active_pendings(sid)) == 1
    _, _, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                         query="yes", session_id=sid,
                         request_policy=ContextPolicy(name="default"))
    assert get.call_count == 1
    assert _searched_query(get) == DRAFT


# --- 3. Retry offer leaves a pending; "yes" re-runs ------------------------------

def test_yes_after_the_retry_offer_reruns_the_search(client, isolate_to_test_vault):
    """Offer -> "yes" -> engines blocked -> retry offer -> "yes" -> search."""
    sid = "sess_test_retry_e2e"
    _seed_pending(sid)
    resp, text, _, get = _turn(
        client, isolate_to_test_vault, [_searxng([], unresponsive=["brave"])],
        query="yes", session_id=sid)
    assert get.call_count == 1
    assert text == RETRY_OFFER_RESPONSE
    assert resp.headers.get("x-ember-web-search") == "unavailable"
    assert [p.metadata["query"] for p in _active_pendings(sid)] == [QUERY]

    resp, text, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                               query="yes", session_id=sid)
    assert get.call_count == 1
    assert _searched_query(get) == QUERY
    assert text == MODEL_REPLY
    assert resp.headers.get("x-ember-web-search") == "true"


# --- 4. Stateless mode -----------------------------------------------------------

def test_stateless_explicit_request_searches(client, isolate_to_test_vault):
    sid = "sess_test_stateless_explicit"
    query = "search the web for the latest bitcoin price"
    resp, text, llm, get = _turn(
        client, isolate_to_test_vault, [_searxng([CLEAN_HIT])], query=query,
        session_id=sid, vault_enabled=False, real_classifier=True)
    assert get.call_count == 1
    assert text == MODEL_REPLY != SCRIPTED_ASK_FIRST_RESPONSE
    assert llm.generate_response.call_args.kwargs["ask_first_active"] is False
    assert resp.headers.get("x-ember-web-search") == "true"


def test_vault_on_explicit_request_searches(client, isolate_to_test_vault):
    """Control: the same request with the vault on, as before."""
    from src.context.policies import _web_search_policy
    sid = "sess_test_vault_explicit"
    resp, text, _, get = _turn(
        client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
        query="search the web for the latest bitcoin price", session_id=sid,
        real_classifier=True, assembly_policy=_web_search_policy(explicit=True))
    assert get.call_count == 1
    assert resp.headers.get("x-ember-web-search") == "true"


def test_stateless_offer_is_held_in_process_and_yes_searches(client, isolate_to_test_vault):
    from src.api.openai_adapter import _stateless_pending_store
    sid = "sess_test_stateless_offer"
    _, text, _, get = _turn(client, isolate_to_test_vault, [], query=QUERY,
                            session_id=sid, vault_enabled=False, reply=OFFER_REPLY)
    assert get.call_count == 0
    assert text == OFFER_REPLY
    # Never in the vault; held in process (the positive half of the check).
    assert _active_pendings(sid) == []
    assert len(_stateless_pending_store) == 1

    resp, text, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                               query="yes", session_id=sid, vault_enabled=False)
    assert get.call_count == 1
    assert _searched_query(get) == QUERY
    assert resp.headers.get("x-ember-web-search") == "true"
    assert len(_stateless_pending_store) == 0


def test_stateless_pending_expires_after_the_ttl():
    from src.api.openai_adapter import _StatelessPendingStore
    now = [1000.0]
    store = _StatelessPendingStore(ttl_seconds=60, clock=lambda: now[0])
    assert store.offer("sess_test_ttl", OFFER_REPLY, QUERY) is True
    now[0] += 59
    assert len(store) == 1
    now[0] += 2
    assert store.take("sess_test_ttl") is None


def test_stateless_pending_is_taken_once_and_needs_an_offer():
    from src.api.openai_adapter import _StatelessPendingStore
    store = _StatelessPendingStore()
    assert store.offer("sess_test_take", "Here is an answer.", QUERY) is False
    assert store.offer("sess_test_take", OFFER_REPLY, QUERY) is True
    assert store.take("sess_test_take")["query"] == QUERY
    assert store.take("sess_test_take") is None


def test_stateless_store_is_bounded():
    from src.api.openai_adapter import _StatelessPendingStore
    store = _StatelessPendingStore(max_sessions=2)
    for i in range(3):
        store.offer(f"sess_test_cap_{i}", OFFER_REPLY, QUERY)
    assert len(store) == 2
    assert store.take("sess_test_cap_0") is None
    assert store.take("sess_test_cap_2") is not None


# --- 5. B2 next-turn dispatch and the dead prefix --------------------------------

def test_b2_follow_up_under_ask_first_searches(client, isolate_to_test_vault):
    """Assembly's classifier is patched to default (it would re-classify the
    bare follow-up); the forced explicit policy must reach build_context."""
    sid = "sess_test_b2"
    resp, _, llm, get = _turn(
        client, isolate_to_test_vault, [_searxng([CLEAN_HIT])], query="the eagles game",
        session_id=sid, awaiting_search=True)
    assert get.call_count == 1
    assert _searched_query(get) == "the eagles game"
    packet = llm.generate_response.call_args.args[0]
    assert packet.web_search_status == "ok"
    assert llm.generate_response.call_args.kwargs["ask_first_active"] is False
    assert resp.headers.get("x-ember-web-search") == "true"


def test_ask_first_prompt_turn_does_not_search_and_gets_a_clean_message(
        client, isolate_to_test_vault):
    """Control for the B2 test (no awaiting flag), and the dead prefix: the
    model gets the user's own words, the ask-first block carries the
    instruction, and the offer writes a pending."""
    sid = "sess_test_prompt_turn"
    _, _, llm, get = _turn(client, isolate_to_test_vault, [], query=QUERY,
                           session_id=sid, reply=OFFER_REPLY)
    assert get.call_count == 0
    assert llm.generate_response.call_args.args[0].user_message == QUERY
    assert llm.generate_response.call_args.kwargs["ask_first_active"] is True
    assert len(_active_pendings(sid)) == 1


def test_offer_to_search_prefix_is_gone():
    source = inspect.getsource(_adapter()._complete_exchange)
    # Positive control: the source read is the backstop's own code.
    assert "_should_search" in source
    assert "Offer to search if appropriate" not in source
    assert "no relevant vault content found" not in source


# --- 6. "searching" status only on turns that search -----------------------------

def _statuses(resp):
    return [e["content"] for e in sse_events(resp.text)
            if isinstance(e, dict) and e.get("type") == "status"]


def test_no_searching_status_on_an_ask_first_prompt_turn(client, isolate_to_test_vault):
    resp, _, _, get = _turn(client, isolate_to_test_vault, [], query=QUERY,
                            session_id="sess_test_status_prompt", stream=True,
                            reply=OFFER_REPLY)
    assert get.call_count == 0
    assert "searching" not in _statuses(resp)


def test_searching_status_on_a_turn_that_searches(client, isolate_to_test_vault):
    """Control for the test above."""
    resp, _, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                            query=QUERY, session_id="sess_test_status_search",
                            stream=True, autonomous=True)
    assert get.call_count == 1
    assert "searching" in _statuses(resp)
