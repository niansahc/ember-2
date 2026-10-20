"""
tests/test_ask_first_pending_lifecycle.py

What happens to an ask-first pending across turns, end to end.

  - A confirmed pending is consumed before the search and generation run
    (resolve-first, the loop guard). When the exchange then ends without a
    stored reply (a generation error, a storage error, a client disconnect,
    the vision_unavailable return), _PendingGuard puts the same offer back,
    so the user's next "yes" still searches. A decline is not put back.
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
import threading

import pytest

from src.memory.exchange import ExchangeStorageError
from tests.conftest import active_pendings, seed_pending
from tests.test_ask_first_routing import (  # noqa: F401  (client is a fixture)
    _adapter,
    CLEAN_HIT,
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


def test_vision_unavailable_restores_the_pending(client, isolate_to_test_vault):
    sid = "sess_test_restore_vision"
    seed_pending(sid, QUERY)
    resp, _, llm, _ = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                            query="yes", session_id=sid, image=True)
    assert resp.status_code == 200
    llm.generate_response.assert_not_called()
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
    from src.context.policies import ContextPolicy
    sid = "sess_test_restore_decline"
    seed_pending(sid, QUERY)
    _turn(client, isolate_to_test_vault, [], query="no thanks", session_id=sid,
          request_policy=ContextPolicy(name="default"),
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
    from src.context.policies import ContextPolicy
    sid = "sess_test_stateless_b2"
    _, text, _, get = _turn(client, isolate_to_test_vault, [], query="google please",
                            session_id=sid, vault_enabled=False, real_classifier=True)
    assert get.call_count == 0
    assert text == "What would you like me to search for?"
    _, text, llm, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                              query="the eagles game", session_id=sid,
                              vault_enabled=False,
                              request_policy=ContextPolicy(name="default"))
    assert get.call_count == 1
    assert _searched_query(get) == "the eagles game"
    assert llm.generate_response.call_args.kwargs["ask_first_active"] is False
    assert len(_store()) == 0


def test_stateless_follow_up_without_clarification_does_not_search(
        client, isolate_to_test_vault):
    """Positive control for the test above: without the clarification turn
    the same message routes to default and searches nothing."""
    from src.context.policies import ContextPolicy
    _, _, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                         query="the eagles game", session_id="sess_test_stateless_b2_ctl",
                         vault_enabled=False,
                         request_policy=ContextPolicy(name="default"))
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
    from src.context.policies import ContextPolicy
    sid = "sess_test_vault_on_drop"
    _stateless_offer(client, isolate_to_test_vault, sid)
    with caplog.at_level(logging.INFO):
        _turn(client, isolate_to_test_vault, [], query="something else",
              session_id=sid, request_policy=ContextPolicy(name="default"))
    assert "[CONFIRM] Dropped stateless entry on a vault-on turn" in caplog.text
    assert len(_store()) == 0
    _, _, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                         query="ok, different topic", session_id=sid,
                         vault_enabled=False,
                         request_policy=ContextPolicy(name="default"))
    assert get.call_count == 0


def test_vault_on_clarification_turn_also_drops_the_stateless_offer(
        client, isolate_to_test_vault):
    """A vault-on turn that ends at the B2 clarification (before Phase B)
    still drops the entry."""
    sid = "sess_test_vault_on_drop_clarify"
    _stateless_offer(client, isolate_to_test_vault, sid)
    _, text, _, _ = _turn(client, isolate_to_test_vault, [], query="google please",
                          session_id=sid, real_classifier=True)
    assert text == "What would you like me to search for?"
    assert len(_store()) == 0


def test_without_a_vault_on_turn_the_stateless_offer_confirms(
        client, isolate_to_test_vault):
    """Positive control for the test above."""
    from src.context.policies import ContextPolicy
    sid = "sess_test_vault_on_drop_ctl"
    _stateless_offer(client, isolate_to_test_vault, sid)
    _, _, _, get = _turn(client, isolate_to_test_vault, [_searxng([CLEAN_HIT])],
                         query="ok, different topic", session_id=sid,
                         vault_enabled=False,
                         request_policy=ContextPolicy(name="default"))
    assert get.call_count == 1


# --- Duplicate guard under concurrency -----------------------------------------

def _ask_first_offer():
    from src.llm.ask_first_validator import SCRIPTED_ASK_FIRST_RESPONSE
    from src.llm.post_gen_pipeline import CodeOffer
    return CodeOffer("ask_first", SCRIPTED_ASK_FIRST_RESPONSE)


def _write_concurrently(sid, queries):
    from src.api.openai_adapter import _write_pending_confirmation
    barrier = threading.Barrier(len(queries))

    def run(query):
        barrier.wait()
        _write_pending_confirmation(_ask_first_offer(), query, sid)

    threads = [threading.Thread(target=run, args=(q,)) for q in queries]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


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

_LEFT_BEHIND = {"held": False}


def test_store_entry_left_by_a_test():
    """Leaves an entry; the conftest teardown must clear it. Its own check
    is the positive control: the entry is live inside this test."""
    assert _store().hold("sess_test_left_behind", QUERY)
    assert len(_store()) == 1
    _LEFT_BEHIND["held"] = True


def test_store_is_empty_in_the_next_test():
    if not _LEFT_BEHIND["held"]:
        pytest.skip("runs after test_store_entry_left_by_a_test")
    assert len(_store()) == 0


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
