"""
tests/test_ask_first_pending_lifecycle.py

What happens to an ask-first pending across turns, end to end.

  - A confirmed pending is consumed before the search and generation run
    (resolve-first, the loop guard). When the exchange then ends without a
    stored reply (a generation error, a storage error, a client disconnect),
    _PendingGuard puts the same offer back, so the user's next "yes" still
    searches, unless the session already holds a newer offer or
    clarification. A decline is not put back. An image exchange consumes no
    offer (it ends one unread), so on the vision_unavailable return only a
    vault-off search clarification can be put back.
  - Stateless (vault toggle off) B2: the clarification's awaiting flag is
    held in process, so the follow-up searches.
  - A vault-on turn drops whatever the session holds in process.
  - The duplicate guard and the pending write run under one lock with a
    fresh read, so concurrent requests in one session write one pending.
  - The in-process store does not outlive a test (conftest).
  - Log lines name what actually happened.

Uses the _turn harness of tests/test_ask_first_routing.py: SearXNG and the
model are mocked; the pending checker and writer are REAL and run against
the conftest throwaway test vault. Synthetic session ids only.
"""

from __future__ import annotations

import logging

import pytest

from unittest.mock import MagicMock, patch

from src.context.policies import SCRIPTED_CLARIFICATION_RESPONSE
from src.memory.exchange import ExchangeStorageError
from tests.conftest import active_pendings, run_in_threads, seed_pending
from tests.test_ask_first_routing import (  # noqa: F401  (client is a fixture)
    _adapter,
    CLEAN_HIT,
    DEFAULT,
    MODEL_REPLY,
    OFFER_REPLY,
    QUERY,
    _searched_query,
    _searxng,
    _turn,
    client,
)



def _store():
    return _adapter()._stateless_pending_store


# --- Restore after an unanswered exchange --------------------------------------

def _yes_again_searches(client, vault, sid, *, vault_enabled=True):
    """The follow-up "yes" searches the stored query."""
    _, text, _, get = _turn(client, vault, [_searxng([CLEAN_HIT])], query="yes",
                            session_id=sid, vault_enabled=vault_enabled)
    assert get.call_count == 1
    assert _searched_query(get) == QUERY
    assert text == MODEL_REPLY


def test_generation_error_restores_the_vault_pending(client, isolate_to_test_vault):
    sid = "sess_test_restore_generation"
    seed_pending(sid, QUERY)
    _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])], query="yes",
          session_id=sid, fail=RuntimeError("model down"))
    assert [p.metadata["query"] for p in active_pendings(sid)] == [QUERY]
    _yes_again_searches(client, isolate_to_test_vault, sid)


def test_generation_error_restores_the_stateless_pending(client, isolate_to_test_vault):
    sid = "sess_test_restore_stateless"
    assert _store().hold(sid, QUERY)
    _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])], query="yes",
          session_id=sid, vault_enabled=False, fail=RuntimeError("model down"))
    assert len(_store()) == 1
    assert active_pendings(sid) == []
    _yes_again_searches(client, isolate_to_test_vault, sid, vault_enabled=False)


def test_stream_generation_error_restores_the_pending(client, isolate_to_test_vault):
    """The SSE guard's on_abort restores, then records the outcome."""
    sid = "sess_test_restore_stream"
    seed_pending(sid, QUERY)
    resp, _, _, _ = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                          query="yes", session_id=sid, stream=True,
                          fail=RuntimeError("model down"))
    assert resp is not None and '"error"' in resp.text
    assert [p.metadata["query"] for p in active_pendings(sid)] == [QUERY]


def test_storage_error_on_the_reply_restores_the_pending(client, isolate_to_test_vault):
    sid = "sess_test_restore_storage"
    seed_pending(sid, QUERY)
    _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])], query="yes",
          session_id=sid, record_reply_error=ExchangeStorageError("reply", OSError()))
    assert [p.metadata["query"] for p in active_pendings(sid)] == [QUERY]


def test_client_disconnect_restores_through_the_guard():
    """guard_sse calls on_abort("interrupted", None) on a disconnect
    (tests/test_sse_guard.py); the guard restores on that call."""
    sid = "sess_test_restore_interrupted"
    guard = _adapter()._PendingGuard(sid, None)
    guard.arm({"query": QUERY, "offer_text": "", "stateless": True})
    assert guard.restore("interrupted", None) is True
    assert _store().take(sid) == {"action": "web_search", "query": QUERY}


def test_answered_exchange_does_not_restore(client, isolate_to_test_vault):
    """Positive control for the restore tests: the same "yes" with a working
    model leaves no pending (the guard was settled when the reply was stored)."""
    sid = "sess_test_restore_control"
    seed_pending(sid, QUERY)
    _, text, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                            query="yes", session_id=sid)
    assert get.call_count == 1 and text == MODEL_REPLY
    assert active_pendings(sid) == []


def test_settled_guard_restores_nothing_and_restores_once():
    guard = _adapter()._PendingGuard("sess_test_guard_once", None)
    guard.arm({"query": QUERY, "offer_text": "", "stateless": True})
    guard.settle()
    assert guard.restore("failed", "RuntimeError") is False
    fresh = _adapter()._PendingGuard("sess_test_guard_once", None)
    fresh.arm({"query": QUERY, "offer_text": "", "stateless": True})
    assert fresh.restore("failed", "RuntimeError") is True
    assert fresh.restore("failed", "RuntimeError") is False


def test_declined_pending_is_not_restored(client, isolate_to_test_vault):
    """A decline settles the offer: a failed exchange does not put it back."""
    sid = "sess_test_restore_decline"
    seed_pending(sid, QUERY)
    _turn(client, isolate_to_test_vault, [], query="no thanks", session_id=sid,
          request_policy=DEFAULT,
          fail=RuntimeError("model down"))
    assert active_pendings(sid) == []


def test_restore_logs_the_outcome(caplog):
    guard = _adapter()._PendingGuard("sess_test_restore_log", None)
    guard.arm({"query": QUERY, "offer_text": "", "stateless": True})
    with caplog.at_level(logging.INFO):
        guard.restore("failed", "RuntimeError")
    assert ("[CONFIRM] Restored pending after unanswered exchange: "
            "outcome=failed reason=RuntimeError store=process") in caplog.text


# --- Stateless B2 next-turn dispatch -------------------------------------------

def test_stateless_b2_follow_up_searches(client, isolate_to_test_vault):
    sid = "sess_test_stateless_b2"
    _, text, _, get = _turn(client, isolate_to_test_vault, [], query="google please",
                            session_id=sid, vault_enabled=False, real_classifier=True)
    assert get.call_count == 0
    assert text == SCRIPTED_CLARIFICATION_RESPONSE
    _, text, llm, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                              query="the eagles game", session_id=sid,
                              vault_enabled=False,
                              request_policy=DEFAULT)
    assert get.call_count == 1
    assert _searched_query(get) == "the eagles game"
    assert llm.generate_response.call_args.kwargs["ask_first_active"] is False
    assert len(_store()) == 0


def test_stateless_follow_up_without_clarification_does_not_search(
        client, isolate_to_test_vault):
    """Positive control for the test above: without the clarification turn
    the same message routes to default and searches nothing."""
    _, _, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                         query="the eagles game", session_id="sess_test_stateless_b2_ctl",
                         vault_enabled=False,
                         request_policy=DEFAULT)
    assert get.call_count == 0


def test_awaiting_entry_is_not_a_search_offer():
    store = _adapter()._StatelessPendingStore()
    assert store.hold_awaiting("sess_test_awaiting")
    assert store.take("sess_test_awaiting") is None
    assert store.take_awaiting("sess_test_awaiting") is True
    assert store.take_awaiting("sess_test_awaiting") is False


# --- A vault-on turn drops the stateless entry ---------------------------------

def _stateless_offer(client, vault, sid):
    _turn(client, vault, [], query=QUERY, session_id=sid, vault_enabled=False,
          reply=OFFER_REPLY)
    assert len(_store()) == 1


def test_vault_on_turn_drops_the_stateless_offer(client, isolate_to_test_vault, caplog):
    sid = "sess_test_vault_on_drop"
    _stateless_offer(client, isolate_to_test_vault, sid)
    with caplog.at_level(logging.INFO):
        _turn(client, isolate_to_test_vault, [], query="something else",
              session_id=sid, request_policy=DEFAULT)
    assert "[CONFIRM] Dropped stateless entry on a vault-on turn" in caplog.text
    assert len(_store()) == 0
    _, _, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                         query="ok", session_id=sid,
                         vault_enabled=False,
                         request_policy=DEFAULT)
    assert get.call_count == 0


def test_vault_on_clarification_turn_also_drops_the_stateless_offer(
        client, isolate_to_test_vault):
    """A vault-on turn that ends at the B2 clarification (before Phase B)
    still drops the entry."""
    sid = "sess_test_vault_on_drop_clarify"
    _stateless_offer(client, isolate_to_test_vault, sid)
    _, text, _, _ = _turn(client, isolate_to_test_vault, [], query="google please",
                          session_id=sid, real_classifier=True)
    assert text == SCRIPTED_CLARIFICATION_RESPONSE
    assert len(_store()) == 0


def test_without_a_vault_on_turn_the_stateless_offer_confirms(
        client, isolate_to_test_vault):
    """Positive control for the test above."""
    sid = "sess_test_vault_on_drop_ctl"
    _stateless_offer(client, isolate_to_test_vault, sid)
    _, _, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                         query="ok", session_id=sid,
                         vault_enabled=False,
                         request_policy=DEFAULT)
    assert get.call_count == 1


# --- Duplicate guard under concurrency -----------------------------------------

def _ask_first_offer():
    from src.llm.ask_first_validator import SCRIPTED_ASK_FIRST_RESPONSE
    from src.llm.post_gen_pipeline import CodeOffer
    return CodeOffer("ask_first", SCRIPTED_ASK_FIRST_RESPONSE)


def _write_concurrently(sid, queries):
    from src.api.openai_adapter import _write_pending_confirmation
    _, errors = run_in_threads(
        lambda i: _write_pending_confirmation(_ask_first_offer(), queries[i], sid),
        len(queries))
    assert errors == []


def test_concurrent_writes_for_one_query_leave_one_pending():
    sid = "sess_test_dedupe_concurrent"
    _write_concurrently(sid, [QUERY, QUERY, QUERY])
    assert len(active_pendings(sid)) == 1


def test_concurrent_writes_for_different_queries_each_write():
    """Positive control for the test above: the guard is per query."""
    sid = "sess_test_dedupe_concurrent_ctl"
    _write_concurrently(sid, ["first synthetic query", "second synthetic query"])
    assert len(active_pendings(sid)) == 2


def test_writer_reads_fresh_after_an_earlier_check():
    """Request B checks (nothing pending), request A writes, then B writes:
    B's guard sees A's pending, so one stays active and a "yes" leaves none."""
    from src.api.openai_adapter import (
        _check_pending_confirmation, _write_pending_confirmation,
    )
    sid = "sess_test_dedupe_interleave"
    assert _check_pending_confirmation(sid, "latest news?") is None
    _write_pending_confirmation(_ask_first_offer(), QUERY, sid)
    _write_pending_confirmation(_ask_first_offer(), QUERY, sid)
    assert len(active_pendings(sid)) == 1
    assert _check_pending_confirmation(sid, "yes")["confirmed"] is True
    assert _check_pending_confirmation(sid, "ok") is None


# --- The in-process store does not outlive a test ------------------------------

def test_teardown_helper_empties_the_store():
    """The conftest teardown helper empties the store. The entry being live
    before the call is the positive control."""
    from tests.conftest import clear_adapter_process_stores
    assert _store().hold("sess_test_left_behind", QUERY)
    assert len(_store()) == 1
    clear_adapter_process_stores()
    assert len(_store()) == 0


def test_every_test_ends_with_the_teardown_helper():
    """restore_process_state (autouse) calls the helper after its yield, so
    no test leaves an entry for the next one, in any order."""
    import ast
    import inspect
    import tests.conftest as conftest
    tree = ast.parse(inspect.getsource(conftest))
    fixture = next(node for node in ast.walk(tree)
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "restore_process_state")
    body = fixture.body
    after_yield = body[next(i for i, stmt in enumerate(body)
                            if isinstance(getattr(stmt, "value", None), ast.Yield)) + 1:]
    called = {node.func.id for stmt in after_yield for node in ast.walk(stmt)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert "clear_adapter_process_stores" in called


# --- The search clarification comes back with an unanswered exchange ------------

def _clarify(client, vault, sid):
    """Vault off: "google please" holds the clarification in process."""
    _, text, _, _ = _turn(client, vault, [], query="google please", session_id=sid,
                          vault_enabled=False, real_classifier=True)
    assert text == SCRIPTED_CLARIFICATION_RESPONSE


@pytest.mark.parametrize("failure", ["generation", "vision"])
def test_unanswered_content_exchange_puts_the_clarification_back(
        client, isolate_to_test_vault, failure):
    sid = f"sess_test_b2_restore_{failure}"
    _clarify(client, isolate_to_test_vault, sid)
    kwargs = {"fail": RuntimeError("boom")} if failure == "generation" else {"image": True}
    _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])], query="the eagles game",
          session_id=sid, vault_enabled=False, **kwargs)
    assert _store().take_awaiting(sid) is True


def test_answered_content_exchange_leaves_no_clarification(client, isolate_to_test_vault):
    """Positive control for the test above: the store is checked, and an
    answered exchange leaves it empty."""
    sid = "sess_test_b2_restore_control"
    _clarify(client, isolate_to_test_vault, sid)
    assert len(_store()) == 1
    _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])], query="the eagles game",
          session_id=sid, vault_enabled=False)
    assert len(_store()) == 0


# --- Restore reports what it did ------------------------------------------------

def test_restore_skips_and_says_so_when_an_active_pending_exists(caplog):
    sid = "sess_test_restore_duplicate"
    seed_pending(sid, QUERY)
    guard = _adapter()._PendingGuard(sid, None)
    guard.arm({"query": QUERY, "offer_text": "Want me to search?", "stateless": False})
    with caplog.at_level(logging.INFO):
        assert guard.restore("failed", "RuntimeError") is False
    assert ("Restore skipped: the session already holds an active entry "
            "(store=vault)") in caplog.text
    assert "Restored pending" not in caplog.text
    assert len(active_pendings(sid)) == 1


def test_restore_without_a_duplicate_writes_and_says_so(caplog):
    """Control for the test above."""
    sid = "sess_test_restore_no_duplicate"
    guard = _adapter()._PendingGuard(sid, None)
    guard.arm({"query": QUERY, "offer_text": "Want me to search?", "stateless": False})
    with caplog.at_level(logging.INFO):
        assert guard.restore("failed", "RuntimeError") is True
    assert "Restored pending" in caplog.text
    assert len(active_pendings(sid)) == 1


# --- A failure after the reply is stored puts nothing back -----------------------

def _fail_after_the_reply_is_stored(client, vault, sid):
    """A confirmed "yes" whose stream breaks after the reply was stored: the
    final [DONE] frame raises, so the stream guard reports a failure."""
    seed_pending(sid, QUERY)
    with patch("src.api.openai_adapter.sse_done", side_effect=RuntimeError("after store")):
        _turn(client, vault, [_searxng([CLEAN_HIT])], query="yes", session_id=sid,
              stream=True)


def test_failure_after_the_reply_is_stored_does_not_restore(client, isolate_to_test_vault):
    sid = "sess_test_settle_wiring"
    _fail_after_the_reply_is_stored(client, isolate_to_test_vault, sid)
    assert active_pendings(sid) == []


def test_without_settle_the_same_failure_restores(client, isolate_to_test_vault, monkeypatch):
    """Positive control for the test above: with _store_reply's settle() out
    of the way, the same failure does put the pending back, so the test above
    sees a missing settle."""
    monkeypatch.setattr(_adapter()._PendingGuard, "settle", lambda self: None)
    sid = "sess_test_settle_wiring_control"
    _fail_after_the_reply_is_stored(client, isolate_to_test_vault, sid)
    assert [p.metadata["query"] for p in active_pendings(sid)] == [QUERY]


# --- Log lines -----------------------------------------------------------------

def test_unmatched_reply_logs_unmatched_not_declined(caplog):
    with caplog.at_level(logging.INFO):
        result = _adapter()._confirmation_result(
            "web_search", QUERY, "What is the population of Tokyo")
    assert result["confirmed"] is False
    assert "Unmatched response (treating as decline)" in caplog.text
    assert "User declined" not in caplog.text


def test_decline_logs_declined(caplog):
    """Control for the test above."""
    with caplog.at_level(logging.INFO):
        _adapter()._confirmation_result("web_search", QUERY, "no thanks")
    assert "User declined" in caplog.text
    assert "Unmatched" not in caplog.text


def test_explicit_request_logs_path_explicit_with_autonomous_on(
        client, isolate_to_test_vault, caplog):
    with caplog.at_level(logging.INFO):
        _, _, _, get = _turn(
            client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
            query="search the web for the latest bitcoin price",
            session_id="sess_test_path_explicit", vault_enabled=False,
            real_classifier=True, autonomous=True)
    assert get.call_count == 1
    assert "[WEB_SEARCH] path=explicit" in caplog.text


def test_autonomous_search_logs_path_autonomous(client, isolate_to_test_vault, caplog):
    """Control for the test above: a non-explicit web_search turn."""
    with caplog.at_level(logging.INFO):
        _, _, _, get = _turn(
            client, isolate_to_test_vault, [_searxng([CLEAN_HIT])], query=QUERY,
            session_id="sess_test_path_autonomous", vault_enabled=False,
            autonomous=True)
    assert get.call_count == 1
    assert "[WEB_SEARCH] path=autonomous" in caplog.text


# --- A failed pending write drops the offer (lean review of PR #302, finding 3) --

_ASK_FIRST_TEXT_WITHOUT_OFFER = "I don't have current information on that."


def _ask_first_offer_reply():
    from src.llm.ask_first_validator import SCRIPTED_ASK_FIRST_RESPONSE
    from src.llm.post_gen_pipeline import CodeOffer
    return SCRIPTED_ASK_FIRST_RESPONSE, CodeOffer("ask_first", SCRIPTED_ASK_FIRST_RESPONSE)


def _store_reply_with(reply, offer, sid, *, exchange=None):
    """_store_reply on a vault-on exchange with a mocked recorder."""
    adapter = _adapter()
    exchange = exchange or MagicMock(vault=None, session_id=sid)
    with patch.object(adapter, "_spawn_deviation_detection"):
        sent = adapter._store_reply(
            exchange, reply, skip_vault_write=False, intent_class="web_search",
            user_turn_text=QUERY, pending_query=QUERY,
            pending_guard=adapter._PendingGuard(sid, None), code_offer=offer)
    return sent, exchange


@pytest.mark.parametrize("which", ["ask_first", "repeat_search"])
def test_failed_pending_write_drops_the_offer_before_it_is_stored(which, caplog):
    from src.llm.ask_first_validator import REPEAT_SEARCH_OFFER
    from src.llm.post_gen_pipeline import CodeOffer
    sid = f"sess_test_write_fails_{which}"
    if which == "ask_first":
        reply, offer = _ask_first_offer_reply()
        expected = _ASK_FIRST_TEXT_WITHOUT_OFFER
    else:
        reply = f"Here is what I know.\n\n{REPEAT_SEARCH_OFFER}"
        offer = CodeOffer("repeat_search", REPEAT_SEARCH_OFFER)
        expected = "Here is what I know."
    with patch.object(_adapter(), "_write_pending_record", side_effect=OSError("disk")), \
            caplog.at_level(logging.INFO):
        sent, exchange = _store_reply_with(reply, offer, sid)
    assert sent == expected
    # The text stored is the text sent.
    exchange.record_reply.assert_called_once_with(expected)
    assert active_pendings(sid) == []
    assert "Offer dropped from the reply" in caplog.text


def test_written_pending_keeps_the_offer():
    """Control for the test above: the same offer whose pending is written is
    stored and sent whole."""
    sid = "sess_test_write_ok"
    reply, offer = _ask_first_offer_reply()
    sent, exchange = _store_reply_with(reply, offer, sid)
    assert sent == reply
    exchange.record_reply.assert_called_once_with(reply)
    assert [p.metadata["query"] for p in active_pendings(sid)] == [QUERY]


def test_failed_reply_write_resolves_the_pending_just_written():
    """The pending is written before the reply. When the reply write then
    fails the user never sees the offer, so its pending must not stay active.
    The control is test_written_pending_keeps_the_offer."""
    sid = "sess_test_reply_write_fails"
    reply, offer = _ask_first_offer_reply()
    exchange = MagicMock(vault=None, session_id=sid)
    exchange.record_reply.side_effect = ExchangeStorageError("reply", OSError())
    with pytest.raises(ExchangeStorageError):
        _store_reply_with(reply, offer, sid, exchange=exchange)
    assert active_pendings(sid) == []


@pytest.mark.parametrize("stream", [False, True])
def test_failed_pending_write_end_to_end_sends_no_offer(client, isolate_to_test_vault,
                                                        stream):
    """An ask-first exchange whose pending write fails: the user sees no
    offer, so a "yes" is never left without a pending to honour."""
    sid = f"sess_test_write_fails_e2e_{stream}"
    with patch("src.api.openai_adapter._write_pending_record",
               side_effect=OSError("disk")):
        _, text, _, _ = _turn(client, isolate_to_test_vault, [], query=QUERY,
                              session_id=sid, stream=stream)
    assert text == _ASK_FIRST_TEXT_WITHOUT_OFFER
    assert active_pendings(sid) == []


# --- A restore never replaces a newer entry (lean review of PR #302, finding 4) --

NEWER_QUERY = "latest ethereum price"


def test_restore_skips_when_the_session_holds_a_newer_vault_offer():
    """An offer made while the failed exchange ran answers the user's next
    "yes"; the consumed one is not put back over it. The control is
    test_restore_without_a_duplicate_writes_and_says_so."""
    sid = "sess_test_restore_newer_vault"
    seed_pending(sid, NEWER_QUERY)
    guard = _adapter()._PendingGuard(sid, None)
    guard.arm({"query": QUERY, "offer_text": "Want me to search?", "stateless": False})
    assert guard.restore("failed", "RuntimeError") is False
    assert [p.metadata["query"] for p in active_pendings(sid)] == [NEWER_QUERY]
    assert _adapter()._check_pending_confirmation(sid, "yes")["query"] == NEWER_QUERY


@pytest.mark.parametrize("newer", ["offer", "clarification"])
def test_restore_skips_when_the_session_holds_a_newer_stateless_entry(newer):
    sid = f"sess_test_restore_newer_stateless_{newer}"
    if newer == "offer":
        assert _store().hold(sid, NEWER_QUERY)
    else:
        assert _store().hold_awaiting(sid)
    guard = _adapter()._PendingGuard(sid, None)
    guard.arm({"query": QUERY, "offer_text": "", "stateless": True})
    assert guard.restore("failed", "RuntimeError") is False
    if newer == "offer":
        assert _store().take(sid) == {"action": "web_search", "query": NEWER_QUERY}
    else:
        assert _store().take_awaiting(sid) is True


def test_restore_of_a_clarification_skips_when_the_session_holds_a_newer_offer():
    sid = "sess_test_restore_awaiting_newer"
    assert _store().hold(sid, NEWER_QUERY)
    guard = _adapter()._PendingGuard(sid, None)
    guard.arm_awaiting()
    assert guard.restore("failed", "RuntimeError") is False
    assert _store().take(sid) == {"action": "web_search", "query": NEWER_QUERY}


@pytest.mark.parametrize("consumed", ["offer", "clarification"])
def test_stateless_restore_with_an_empty_store_puts_it_back(consumed):
    """Control for the two tests above: with nothing newer held, the
    consumed entry is put back."""
    sid = f"sess_test_restore_stateless_empty_{consumed}"
    guard = _adapter()._PendingGuard(sid, None)
    if consumed == "offer":
        guard.arm({"query": QUERY, "offer_text": "", "stateless": True})
    else:
        guard.arm_awaiting()
    assert guard.restore("failed", "RuntimeError") is True
    if consumed == "offer":
        assert _store().take(sid) == {"action": "web_search", "query": QUERY}
    else:
        assert _store().take_awaiting(sid) is True
