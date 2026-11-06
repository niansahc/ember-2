"""
tests/test_turn_lock.py

Same-session chat turns run one at a time; other sessions and other endpoints
stay concurrent. Turns moved off the event loop stopped being serialized by
it, which let two turns on one session consume one pending confirmation, create
one session twice, and race the pending task offers.

Positive control for each: the same scenario with the lock replaced by one that
never waits reproduces the defect.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.responses import StreamingResponse

import src.api.openai_adapter as adapter
import src.api.turn_lock as turn_lock
import src.memory.exchange as exchange_mod
import src.memory.session as session_mod
import src.tasks.task_handler as task_handler
import src.tools.web_search as web_search_mod
from src.api.sse import guard_sse, sse_done
from src.api.turn_lock import TurnBusy, TurnLocks, TurnStreamingResponse
from tests.conftest import run_in_threads, seed_pending
from tests.test_ask_first_routing import CLEAN_HIT, MODEL_REPLY, QUERY, _searxng


def _post(client, message, session_id, stream=False):
    return client.post(
        "/v1/chat/completions",
        json={"model": "ember", "stream": stream, "vault_enabled": True,
              "messages": [{"role": "user", "content": message}]},
        headers={"X-Session-ID": session_id},
    )


@pytest.fixture
def env(keyless_client):
    """(client, the SearXNG request mock, the llm_adapter mock). No model and
    no search engine is called."""
    from src.context.policies import _web_search_policy

    with ExitStack() as stack:
        search = stack.enter_context(patch.object(
            web_search_mod.requests, "get",
            side_effect=lambda *a, **k: _searxng([CLEAN_HIT])))
        stack.enter_context(patch.object(
            web_search_mod, "get_web_search_min_term_hits", return_value=0))
        stack.enter_context(patch(
            "src.context.policies.classify_query",
            return_value=_web_search_policy(explicit=False)))
        stack.enter_context(patch.object(
            adapter.context_service.retriever, "retrieve",
            return_value=([], [], [], [], None)))
        llm = stack.enter_context(patch("src.api.openai_adapter.llm_adapter"))
        llm.generate_response.return_value = MODEL_REPLY
        llm.generate_response_iter.side_effect = lambda *a, **k: iter([MODEL_REPLY])
        for name in ("_background_state_extraction", "_detect_and_write_commitment",
                     "_detect_task_in_response", "_background_topic_decline_resolution",
                     "_spawn_deviation_detection", "_spawn_post_exchange_extractors"):
            stack.enter_context(patch(f"src.api.openai_adapter.{name}"))
        onboarding = stack.enter_context(patch("src.api.openai_adapter.onboarding_service"))
        onboarding.is_active.return_value = False
        stack.enter_context(patch(
            "src.core.preferences.get",
            side_effect=lambda k, d=None, *a, **kw:
                False if k == "web_search_autonomous" else d))
        stack.enter_context(patch(
            "src.api.openai_adapter._prior_turn_awaits_search_content",
            return_value=False))
        stack.enter_context(patch(
            "src.llm.coaching_filter.filter_coaching_frame",
            new=MagicMock(side_effect=lambda r, *a, **k: r)))
        stack.enter_context(patch(
            "src.safety.grounding_check.should_short_circuit_grounding",
            return_value=False))
        stack.enter_context(patch(
            "src.safety.grounding_check.run_grounding_check",
            new=AsyncMock(return_value=(True, None))))
        stack.enter_context(patch(
            "ollama.embed", return_value={"embeddings": [[0.0] * 768]}))
        with keyless_client as client:
            yield client, search, llm


class _NoWaitLocks:
    """Never waits: turns overlap, as they did with no lock."""

    async def acquire(self, session_id):
        return MagicMock()


@pytest.fixture
def no_lock(monkeypatch):
    monkeypatch.setattr(adapter, "_turn_locks", _NoWaitLocks())


# --- Same-session effects -------------------------------------------------


def _double_yes(client):
    """Two "yes" at once on one session with one pending. Returns how many
    turns read it as confirmed."""
    session_id = "sess_test_lock_confirm"
    seed_pending(session_id, QUERY)
    real_check = adapter._check_pending_confirmation
    real_resolve = adapter._resolve_original_pending
    confirmed = []

    def spy(*args, **kwargs):
        result = real_check(*args, **kwargs)
        confirmed.append(bool(result and result.get("confirmed")))
        return result

    def slow_resolve(*args, **kwargs):
        time.sleep(0.3)
        return real_resolve(*args, **kwargs)

    with patch.object(adapter, "_check_pending_confirmation", spy), \
         patch.object(adapter, "_resolve_original_pending", slow_resolve):
        results, errors = run_in_threads(lambda _i: _post(client, "yes", session_id), 2)
    assert errors == []
    assert [r.status_code for r in results] == [200, 200]
    return sum(confirmed)


def test_two_confirmations_on_one_session_fire_once(env):
    client, search, _ = env
    assert _double_yes(client) == 1
    assert search.call_count == 1


def test_control_without_the_lock_both_confirm(env, no_lock):
    client, search, _ = env
    assert _double_yes(client) == 2
    assert search.call_count == 2


def _session_records(session_id):
    return [r for r in session_mod._read_all_session_records()
            if r.get("metadata", {}).get("session_id") == session_id]


def _two_first_turns(client, session_id):
    real_exists = exchange_mod.session_exists

    def slow_exists(sid):
        found = real_exists(sid)
        time.sleep(0.2)
        return found

    with patch.object(exchange_mod, "session_exists", slow_exists):
        _, errors = run_in_threads(lambda _i: _post(client, "hello there", session_id), 2)
    assert errors == []
    return len(_session_records(session_id))


def test_two_first_turns_create_one_session(env):
    client, _, _ = env
    assert _two_first_turns(client, "sess_test_lock_create") == 1


def test_control_without_the_lock_two_sessions_are_created(env, no_lock):
    client, _, _ = env
    assert _two_first_turns(client, "sess_test_lock_create_ctl") == 2


# --- Pending task offers --------------------------------------------------


def test_only_one_caller_gets_the_pending_offers():
    """Guaranteed by the single pop(session_id, None), not by the lock; the
    control below is the check-then-pop form it replaced."""
    session_id = "sess_test_lock_offers"
    task_handler.store_pending_offer(session_id, "Write the synthetic report")

    results, errors = run_in_threads(
        lambda _i: task_handler.check_pending_confirmation(session_id, "no"), 8)

    assert errors == []
    assert sum(r is not None for r in results) == 1


class _SlowContains(dict):
    def __contains__(self, key):
        found = super().__contains__(key)
        time.sleep(0.1)
        return found


def test_control_check_then_pop_raises_keyerror():
    """The prior form of the pop. Shows the harness reaches the window that
    the test above relies on being closed."""
    offers = _SlowContains({"s": ["t"]})

    def check_then_pop(_i):
        if "s" in offers:
            offers.pop("s")

    _, errors = run_in_threads(check_then_pop, 2)
    assert len(errors) == 1 and isinstance(errors[0], KeyError)


class _SlowSetdefault(dict):
    """Holds a store between creating the session's list and appending to
    it, the window where a pop can take the list away."""

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()

    def setdefault(self, key, default=None):
        value = super().setdefault(key, default)
        self.entered.set()
        time.sleep(0.3)
        return value


def _store_during_a_pop(monkeypatch) -> list[str]:
    """A store is between setdefault and append when a pop runs. Returns the
    offers that survive, popped or still stored."""
    session_id = "sess_test_lock_store"
    offers = _SlowSetdefault()
    monkeypatch.setattr(task_handler, "_pending_offers", offers)
    popped: list[str] = []

    def pop_once_the_store_is_inside():
        assert offers.entered.wait(5)
        result = task_handler.check_pending_confirmation(session_id, "no")
        popped.extend(result.task_titles if result else [])

    popper = threading.Thread(target=pop_once_the_store_is_inside)
    popper.start()
    task_handler.store_pending_offer(session_id, "Write the synthetic report")
    popper.join(5)
    return popped + offers.get(session_id, [])


def test_an_offer_stored_during_a_pop_is_kept(monkeypatch):
    assert _store_during_a_pop(monkeypatch) == ["Write the synthetic report"]


def test_control_without_the_offers_lock_the_offer_is_lost(monkeypatch):
    monkeypatch.setattr(task_handler, "_offers_lock", contextlib.nullcontext())
    assert _store_during_a_pop(monkeypatch) == []


# --- Turns through the route ----------------------------------------------


def _timed_generate(events):
    def generate(*_a, **_k):
        events.append("start")
        time.sleep(0.2)
        events.append("end")
        yield MODEL_REPLY
    return generate


def test_same_session_streams_do_not_interleave(env):
    client, _, llm = env
    events: list[str] = []
    llm.generate_response_iter.side_effect = _timed_generate(events)

    results, errors = run_in_threads(
        lambda _i: _post(client, "tell me more", "sess_test_lock_order", stream=True), 2)

    assert errors == []
    assert all("[DONE]" in r.text for r in results)
    assert events == ["start", "end", "start", "end"]


def test_control_without_the_lock_streams_interleave(env, no_lock):
    client, _, llm = env
    events: list[str] = []
    llm.generate_response_iter.side_effect = _timed_generate(events)

    _, errors = run_in_threads(
        lambda _i: _post(client, "tell me more", "sess_test_lock_order_ctl", stream=True), 2)

    assert errors == []
    assert events[:2] == ["start", "start"]


def test_different_sessions_run_at_once(env):
    client, _, llm = env
    # Passes only if both turns are inside generation together.
    barrier = threading.Barrier(2, timeout=10)

    def generate(*_a, **_k):
        barrier.wait()
        yield MODEL_REPLY

    llm.generate_response_iter.side_effect = generate
    results, errors = run_in_threads(
        lambda i: _post(client, "tell me more", f"sess_test_lock_other_{i}", stream=True), 2)

    assert errors == []
    assert all("[DONE]" in r.text and '"type": "error"' not in r.text for r in results)


@pytest.fixture
def held_turn(env, monkeypatch):
    """A first turn parked inside generation on sess_test_lock_busy, and a
    short wait limit. Yields (client, release)."""
    client, _, llm = env
    monkeypatch.setattr(turn_lock, "TURN_WAIT_TIMEOUT_S", 0.3)
    entered = threading.Event()
    release = threading.Event()

    def generate(*_a, **_k):
        if not entered.is_set():  # only the first turn parks
            entered.set()
            release.wait(20)
        yield MODEL_REPLY

    llm.generate_response_iter.side_effect = generate
    first = threading.Thread(
        target=_post, args=(client, "tell me more", "sess_test_lock_busy"),
        kwargs={"stream": True}, daemon=True)
    first.start()
    assert entered.wait(10)
    try:
        yield client, release
    finally:
        release.set()
        first.join(10)


def test_a_turn_that_cannot_get_in_answers_with_failure_frames(held_turn):
    client, _ = held_turn
    resp = _post(client, "are you there", "sess_test_lock_busy", stream=True)

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert '"code": "generation_failed"' in resp.text
    assert sse_done() in resp.text


def test_a_non_streaming_turn_that_cannot_get_in_gets_503(held_turn):
    client, _ = held_turn
    assert _post(client, "are you there", "sess_test_lock_busy").status_code == 503


def test_other_sessions_and_health_answer_during_a_held_turn(held_turn):
    client, _ = held_turn
    assert client.get("/api/health").status_code == 200
    other = _post(client, "tell me more", "sess_test_lock_free", stream=True)
    assert "[DONE]" in other.text and '"type": "error"' not in other.text


def test_the_lock_store_is_empty_after_turns(env):
    client, _, _ = env
    _post(client, "hello there", "sess_test_lock_clean", stream=True)
    _post(client, "hello there", "sess_test_lock_clean")
    assert adapter._turn_locks._entries == {}


# --- TurnLocks ------------------------------------------------------------


def test_second_acquire_waits_for_release():
    async def scenario():
        locks = TurnLocks()
        first = await locks.acquire("s")
        waiter = asyncio.ensure_future(locks.acquire("s"))
        await asyncio.sleep(0.05)
        assert not waiter.done()
        first.release()
        second = await asyncio.wait_for(waiter, 1)
        second.release()
        return locks

    assert asyncio.run(scenario())._entries == {}


def test_other_sessions_do_not_wait():
    async def scenario():
        locks = TurnLocks()
        first = await locks.acquire("s")
        other = await asyncio.wait_for(locks.acquire("t"), 1)
        first.release()
        other.release()

    asyncio.run(scenario())


def test_timeout_raises_turnbusy_and_cleans_up(monkeypatch):
    monkeypatch.setattr(turn_lock, "TURN_WAIT_TIMEOUT_S", 0.05)

    async def scenario():
        locks = TurnLocks()
        held = await locks.acquire("s")
        with pytest.raises(TurnBusy):
            await locks.acquire("s")
        assert list(locks._entries) == ["s"]
        held.release()
        return locks

    assert asyncio.run(scenario())._entries == {}


def test_release_twice_is_harmless():
    async def scenario():
        locks = TurnLocks()
        lease = await locks.acquire("s")
        lease.release()
        lease.release()
        again = await asyncio.wait_for(locks.acquire("s"), 1)
        again.release()

    asyncio.run(scenario())


# --- TurnStreamingResponse ------------------------------------------------


def _scope():
    return {"type": "http", "asgi": {"spec_version": "2.4"}, "method": "POST",
            "path": "/", "headers": []}


async def _receive():
    await asyncio.sleep(3600)


def _guarded_response(order, frames=2):
    async def body():
        for _ in range(frames):
            yield "data: x\n\n"
            await asyncio.sleep(0)
        yield sse_done()

    def on_abort(outcome, reason):
        order.append(f"abort:{outcome}")

    inner = StreamingResponse(guard_sse(body(), "c1", on_abort=on_abort))
    lease = MagicMock()
    lease.release.side_effect = lambda: order.append("release")
    return TurnStreamingResponse(inner, lease), lease


def test_the_response_keeps_the_inner_headers():
    inner = StreamingResponse(iter([]), headers={"X-Ember-Vision-Used": "true"},
                              media_type="text/event-stream")
    wrapped = TurnStreamingResponse(inner, MagicMock())
    assert wrapped.headers["x-ember-vision-used"] == "true"
    assert wrapped.media_type == "text/event-stream"


def test_release_follows_a_complete_response():
    order: list[str] = []
    response, lease = _guarded_response(order)

    async def send(_message):
        pass

    asyncio.run(response(_scope(), _receive, send))
    assert order == ["release"]
    lease.release.assert_called_once()


def test_a_failed_send_runs_on_abort_before_release():
    order: list[str] = []
    response, _ = _guarded_response(order)

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("client went away")

    async def scenario():
        with pytest.raises(BaseException):
            await response(_scope(), _receive, send)

    asyncio.run(scenario())
    assert order == ["abort:interrupted", "release"]


def test_cancellation_runs_on_abort_before_release():
    order: list[str] = []
    response, _ = _guarded_response(order)

    async def scenario():
        event = asyncio.Event()

        async def send(message):
            if message["type"] == "http.response.body":
                event.set()
                await asyncio.sleep(3600)

        task = asyncio.ensure_future(response(_scope(), _receive, send))
        await event.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert order == ["abort:interrupted", "release"]


def test_control_without_closing_the_body_release_comes_first(monkeypatch):
    """Generator finalization runs on_abort late, after the next turn could
    have started. The explicit close is what puts it first."""
    order: list[str] = []
    response, _ = _guarded_response(order)
    monkeypatch.setattr(turn_lock, "_close_body", AsyncMock())

    async def send(message):
        if message["type"] == "http.response.body":
            raise OSError("client went away")

    async def scenario():
        with pytest.raises(BaseException):
            await response(_scope(), _receive, send)

    asyncio.run(scenario())
    assert order[0] == "release"
