"""
tests/test_event_loop_liveness.py

A slow model call must not stall the API. On 2026-11-01 a wedged local
auxiliary call (the coaching filter's semantic check) ran inline on the event
loop and froze every request, /api/health included, for 290 s.

Each scenario sends one turn (or one upload) whose blocking step parks on a
threading.Event, then issues GET /api/health from a second thread while the
step is provably still parked. `with TestClient(app)` gives both threads one
shared event loop, so a blocked loop cannot answer health exactly as under
uvicorn.

The pass condition has no latency threshold. A free loop answers health while
the step is parked, however slowly a loaded machine schedules it; a blocked
loop cannot answer until the step is released. The earlier version asserted
"slowest health sample < 1.0 s". That bound sat about 2x above a synchronous
stretch of the chat path unrelated to the stalled call (a TLS context built on
the loop in the grounding revision pass: 0.3-0.5 s unloaded, 0.6-0.9 s under
CPU load) and above thread-scheduling hiccups, so it flaked under load
(2 of 10 file runs failed with 16 CPU burners). A wall-clock bound cannot tell
"loop blocked" from "machine busy"; the release ordering can.

Positive control (same commit, per CLAUDE.md "Testing Discipline"): the same
scenario with the off-loop helpers replaced by inline callers, which is the
pre-fix behavior. The loop must stay blocked until release: a callback
scheduled on the loop must not run before release, and the health call must
complete after release. If the loop answers early, the control fails, so the
liveness tests cannot stay green on a harness that cannot see a block.
"""

import threading
import time
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

import pytest

# Only time bound: a hang guard so a broken test cannot wedge the suite. It is
# never a latency assertion.
HANG_GUARD_S = 30.0
HEALTH_CALLS = 5
# Positive control: how long to wait for a callback to run on the loop before
# concluding it is blocked.
BLOCK_GRACE_S = 2.0


class Stall:
    """A blocking step that parks until the test releases it."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.released_at = None

    def block(self):
        """Called from the stalled step. Parks until release (or the guard)."""
        self.entered.set()
        if not self.release.wait(HANG_GUARD_S):
            raise AssertionError("stalled step was never released")

    def do_release(self):
        self.released_at = time.perf_counter()
        self.release.set()


@pytest.fixture
def stall():
    s = Stall()
    yield s
    s.release.set()  # never leave a worker parked if a test failed early


def _mock_ollama_chat_for(stall):
    def _mock_ollama_chat(**kwargs):
        """Fake ollama.chat. The coaching filter's semantic identity check
        (num_predict=10) is the stalled call; everything else answers at once."""
        options = kwargs.get("options") or {}
        if options.get("num_predict") == 10:
            stall.block()
            return {"message": {"content": "NO"}}
        if kwargs.get("stream"):
            return iter([{"message": {"content": "A short reply."}}])
        return {"message": {"content": "A short reply."}}
    return _mock_ollama_chat


async def _inline_call(fn, *args, **kwargs):
    """The pre-fix behavior: run the blocking function on the loop."""
    return fn(*args, **kwargs)


def _inline_iter(iterator):
    """The pre-fix behavior: iterate a sync generator on the loop."""
    async def _gen():
        for item in iterator:
            yield item
    return _gen()


class _Requester(threading.Thread):
    """Runs the request under test on its own thread."""

    def __init__(self, request_fn):
        super().__init__(daemon=True)
        self._request_fn = request_fn
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self._request_fn()
        except BaseException as exc:  # surfaced by the test, not swallowed
            self.error = exc


class _HealthCaller(threading.Thread):
    """Issues GET /api/health `count` times; records each completion time."""

    def __init__(self, client, count):
        super().__init__(daemon=True)
        self._client = client
        self._count = count
        self.completed_at = []
        self.statuses = []

    def run(self):
        for _ in range(self._count):
            resp = self._client.get("/api/health")
            self.statuses.append(resp.status_code)
            self.completed_at.append(time.perf_counter())


class _LoopProbe(threading.Thread):
    """Runs a trivial coroutine on the app's event loop and records when the
    loop executed it."""

    def __init__(self, client):
        super().__init__(daemon=True)
        self._client = client
        self.ran_at = None

    def run(self):
        async def _mark():
            self.ran_at = time.perf_counter()

        self._client.portal.call(_mark)


def _start_request(request_fn, stall):
    requester = _Requester(request_fn)
    requester.start()
    assert stall.entered.wait(HANG_GUARD_S), "the stalled step was never reached"
    return requester


def _finish_request(requester):
    requester.join(HANG_GUARD_S)
    assert not requester.is_alive(), "request did not finish after release"
    assert requester.error is None, repr(requester.error)
    return requester.result


def _run_while_stalled(client, request_fn, stall):
    """Liveness scenario. Health must be answered while the step is parked.
    Returns the request's response."""
    requester = _start_request(request_fn, stall)
    health = _HealthCaller(client, HEALTH_CALLS)
    health.start()
    health.join(HANG_GUARD_S)
    answered_while_parked = (
        not health.is_alive() and len(health.completed_at) == HEALTH_CALLS)
    stall.do_release()
    health.join(HANG_GUARD_S)
    resp = _finish_request(requester)

    assert answered_while_parked, (
        "health did not answer while the stalled step was parked: "
        "the event loop was blocked")
    # Every completion preceded the release: answered during the stall.
    assert max(health.completed_at) < stall.released_at
    assert health.statuses == [200] * HEALTH_CALLS
    return resp


def _run_while_blocked(client, request_fn, stall):
    """Positive control. The loop must stay blocked until release. Returns the
    request's response."""
    requester = _start_request(request_fn, stall)
    health = _HealthCaller(client, 1)
    health.start()

    probe = _LoopProbe(client)
    probe.start()
    # portal calls block until the loop runs them, so they go on their own
    # thread. A blocked loop never runs the callback; a free one runs it in
    # milliseconds even under heavy CPU load.
    probe.join(BLOCK_GRACE_S)
    ran_early = not probe.is_alive()

    stall.do_release()
    probe.join(HANG_GUARD_S)
    health.join(HANG_GUARD_S)
    resp = _finish_request(requester)

    assert not ran_early, "the loop ran a callback while the step was parked"
    assert probe.ran_at is not None and probe.ran_at >= stall.released_at
    assert health.completed_at, "health never completed after release"
    assert health.completed_at[0] >= stall.released_at
    return resp


def _chat(client, *, stream):
    return client.post(
        "/v1/chat/completions",
        json={
            "model": "ember-2",
            "messages": [{"role": "user", "content": "What am I working on right now?"}],
            "stream": stream,
        },
        headers={"X-Test-Session": "true"},
    )


@pytest.fixture
def live_client(keyless_client, stall):
    with ExitStack() as stack:
        stack.enter_context(
            patch("ollama.chat", side_effect=_mock_ollama_chat_for(stall)))
        stack.enter_context(
            patch("ollama.embed", return_value={"embeddings": [[0.0] * 768]}))
        stack.enter_context(patch(
            "src.safety.grounding_check.run_grounding_check",
            new=AsyncMock(return_value=(True, None))))
        # An empty test vault would otherwise start onboarding, which
        # answers before generation and never reaches the stalled step.
        onboarding = stack.enter_context(
            patch("src.api.openai_adapter.onboarding_service"))
        onboarding.is_active.return_value = False
        with keyless_client as client:
            yield client


@pytest.fixture
def pre_fix_behavior():
    """Replace the chat path's off-loop helpers with inline callers. The ingest
    routes have their own, patched in the ingest control."""
    with patch("src.api.openai_adapter._off_loop", new=_inline_call), \
         patch("src.api.openai_adapter._iter_off_loop", new=_inline_iter):
        yield


def _upload(client):
    def send():
        return client.post(
            "/ingest/upload",
            files={"file": ("notes.txt", b"plain text for the upload", "text/plain")},
        )
    return send


def _import_json(client):
    def send():
        return client.post("/ingest/json", json=[{"text": "synthetic record"}])
    return send


@contextmanager
def _slow_ingest(stall):
    """Both ingest routes stall in the blocking half they hand to a worker."""
    def _stall(*_args, **_kwargs):
        stall.block()
        return {"imported": 1, "errors": []}

    with patch("src.api.routes.ingest.write_chunks_to_vault", side_effect=_stall), \
         patch("src.api.routes.ingest._import_json_records", side_effect=_stall):
        yield


class TestHealthAnswersWhileATurnIsStalled:

    def test_streaming_turn(self, live_client, stall):
        resp = _run_while_stalled(
            live_client, lambda: _chat(live_client, stream=True), stall)
        assert resp.status_code == 200
        assert "[DONE]" in resp.text

    def test_non_streaming_turn(self, live_client, stall):
        resp = _run_while_stalled(
            live_client, lambda: _chat(live_client, stream=False), stall)
        assert resp.status_code == 200
        assert "choices" in resp.json()

    def test_upload(self, live_client, stall):
        with _slow_ingest(stall):
            resp = _run_while_stalled(live_client, _upload(live_client), stall)
        assert resp.status_code == 200

    def test_json_import(self, live_client, stall):
        with _slow_ingest(stall):
            resp = _run_while_stalled(live_client, _import_json(live_client), stall)
        assert resp.status_code == 200


class TestPositiveControl:
    """The pre-fix path blocks the loop. Without these, the tests above would
    stay green on a harness that cannot see a block."""

    def test_streaming_turn_blocks_health_when_run_inline(
        self, live_client, stall, pre_fix_behavior,
    ):
        resp = _run_while_blocked(
            live_client, lambda: _chat(live_client, stream=True), stall)
        assert resp.status_code == 200

    def test_non_streaming_turn_blocks_health_when_run_inline(
        self, live_client, stall, pre_fix_behavior,
    ):
        resp = _run_while_blocked(
            live_client, lambda: _chat(live_client, stream=False), stall)
        assert resp.status_code == 200

    @pytest.mark.parametrize("request_fn", [_upload, _import_json],
                             ids=["upload", "json-import"])
    def test_ingest_blocks_health_when_run_inline(
        self, live_client, stall, request_fn,
    ):
        with _slow_ingest(stall), \
             patch("src.api.routes.ingest.run_in_threadpool", new=_inline_call):
            resp = _run_while_blocked(live_client, request_fn(live_client), stall)
        assert resp.status_code == 200
