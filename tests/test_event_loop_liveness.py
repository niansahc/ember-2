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
loop cannot answer until the step is released. A wall-clock bound cannot tell
"loop blocked" from "machine busy" (the earlier 1.0 s bound flaked under CPU
load); the release ordering can.

Positive control (same commit, per CLAUDE.md "Testing Discipline"): the same
scenario with the off-loop helpers replaced by inline callers, which is the
pre-fix behavior. Health must not be answered while the step is parked. If it
is, the control fails, so the liveness tests cannot stay green on a harness
that cannot see a block.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

import pytest

# Hang guard so a broken test cannot wedge the suite. Never a latency
# assertion: the liveness wait returns as soon as health answers.
HANG_GUARD_S = 30.0
# Positive control: how long health gets to answer before the loop is
# concluded blocked. A free loop answers in milliseconds even under heavy load.
BLOCK_GRACE_S = 2.0


class Stall:
    """A blocking step that parks until the test releases it."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def block(self):
        """Called from the stalled step. Parks until release (or the guard)."""
        self.entered.set()
        if not self.release.wait(HANG_GUARD_S):
            raise AssertionError("stalled step was never released")


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


def _health_during_stall(client, request_fn, stall, wait_s):
    """Run request_fn until its blocking step parks, then issue GET
    /api/health and give it up to wait_s to answer while the step is still
    parked. Release the step and let both finish.

    Returns (request response, health answered while parked, health status).
    Worker exceptions re-raise here with their own traceback.
    """
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            request = pool.submit(request_fn)
            if not stall.entered.wait(HANG_GUARD_S):
                if request.done():
                    request.result()  # surface the request's own error
                raise AssertionError("the stalled step was never reached")
            health = pool.submit(client.get, "/api/health")
            wait([health], timeout=wait_s)
            # Sampled before release, so True means answered during the stall.
            answered_while_parked = health.done()
        finally:
            stall.release.set()
        health_status = health.result(timeout=HANG_GUARD_S).status_code
        resp = request.result(timeout=HANG_GUARD_S)
    return resp, answered_while_parked, health_status


def _run_while_stalled(client, request_fn, stall):
    """Liveness scenario. Health must be answered while the step is parked.
    Returns the request's response."""
    resp, answered, health_status = _health_during_stall(
        client, request_fn, stall, HANG_GUARD_S)
    assert answered, (
        "health did not answer while the stalled step was parked: "
        "the event loop was blocked")
    assert health_status == 200
    return resp


def _run_while_blocked(client, request_fn, stall):
    """Positive control. Health must not be answered until release. Returns
    the request's response."""
    resp, answered, health_status = _health_during_stall(
        client, request_fn, stall, BLOCK_GRACE_S)
    assert not answered, "health answered while the step was parked"
    assert health_status == 200
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
        # The empty test vault short-circuits grounding straight into the
        # revision pass, which would otherwise make a real HTTP call.
        stack.enter_context(patch(
            "src.safety.grounding_check.run_revision_pass",
            new=AsyncMock(side_effect=lambda response, *_a, **_k: response)))
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
