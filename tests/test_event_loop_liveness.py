"""
tests/test_event_loop_liveness.py

A slow model call must not stall the API. On 2026-11-01 a wedged local
auxiliary call (the coaching filter's semantic check) ran inline on the event
loop and froze every request, /api/health included, for 290 s.

Each scenario sends one turn (or one upload) whose blocking step sleeps, and
polls GET /api/health from a second thread. `with TestClient(app)` gives both
threads one shared event loop, so a blocked loop shows up as slow health
responses exactly as it does under uvicorn.

Positive control (same commit, per CLAUDE.md "Testing Discipline"): the same
scenario with the off-loop helpers replaced by inline callers, which is the
pre-fix behavior. It must block; if it does not, the liveness bound proves
nothing.
"""

import threading
import time
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

SLEEP_S = 2.0
LIVENESS_BOUND_S = 1.0
POLL_EVERY_S = 0.1


def _mock_ollama_chat(**kwargs):
    """Fake ollama.chat. The coaching filter's semantic identity check
    (num_predict=10) is the stalled call; everything else answers at once."""
    options = kwargs.get("options") or {}
    if options.get("num_predict") == 10:
        time.sleep(SLEEP_S)
        return {"message": {"content": "NO"}}
    if kwargs.get("stream"):
        return iter([{"message": {"content": "A short reply."}}])
    return {"message": {"content": "A short reply."}}


async def _inline_call(fn, *args, **kwargs):
    """The pre-fix behavior: run the blocking function on the loop."""
    return fn(*args, **kwargs)


def _inline_iter(iterator):
    """The pre-fix behavior: iterate a sync generator on the loop."""
    async def _gen():
        for item in iterator:
            yield item
    return _gen()


def _poll_health(client, stop, latencies):
    while not stop.is_set():
        start = time.perf_counter()
        resp = client.get("/api/health")
        assert resp.status_code == 200
        latencies.append(time.perf_counter() - start)
        stop.wait(POLL_EVERY_S)


def _run_while_polling(client, request_fn):
    """Run request_fn on this thread while a second thread polls health.
    Returns (request_result, health_latencies)."""
    stop = threading.Event()
    latencies: list[float] = []
    poller = threading.Thread(
        target=_poll_health, args=(client, stop, latencies), daemon=True)
    poller.start()
    time.sleep(0.3)  # let the poller get a few fast samples in first
    try:
        result = request_fn()
    finally:
        stop.set()
        poller.join(timeout=10)
    return result, latencies


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
def live_client(keyless_client):
    with ExitStack() as stack:
        stack.enter_context(patch("ollama.chat", side_effect=_mock_ollama_chat))
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
    """Replace the off-loop helpers with inline callers."""
    with patch("src.api.openai_adapter._off_loop", new=_inline_call), \
         patch("src.api.openai_adapter._iter_off_loop", new=_inline_iter):
        yield


class TestHealthAnswersWhileATurnIsStalled:

    def test_streaming_turn(self, live_client):
        resp, latencies = _run_while_polling(
            live_client, lambda: _chat(live_client, stream=True))
        assert resp.status_code == 200
        assert "[DONE]" in resp.text
        assert len(latencies) >= 5, "poller did not run during the turn"
        assert max(latencies) < LIVENESS_BOUND_S

    def test_non_streaming_turn(self, live_client):
        resp, latencies = _run_while_polling(
            live_client, lambda: _chat(live_client, stream=False))
        assert resp.status_code == 200
        assert "choices" in resp.json()
        assert len(latencies) >= 5
        assert max(latencies) < LIVENESS_BOUND_S

    def test_upload(self, live_client):
        def slow_write(chunks, vault_path):
            time.sleep(SLEEP_S)

        def upload():
            return live_client.post(
                "/ingest/upload",
                files={"file": ("notes.txt", b"plain text for the upload", "text/plain")},
            )

        with patch("src.api.routes.ingest.write_chunks_to_vault", side_effect=slow_write):
            resp, latencies = _run_while_polling(live_client, upload)
        assert resp.status_code == 200
        assert len(latencies) >= 5
        assert max(latencies) < LIVENESS_BOUND_S


class TestPositiveControl:
    """The pre-fix path blocks the loop. Without these, the tests above would
    stay green on a harness that cannot see a block."""

    def test_streaming_turn_blocks_health_when_run_inline(
        self, live_client, pre_fix_behavior,
    ):
        resp, latencies = _run_while_polling(
            live_client, lambda: _chat(live_client, stream=True))
        assert resp.status_code == 200
        assert max(latencies) >= SLEEP_S - 1.0

    def test_non_streaming_turn_blocks_health_when_run_inline(
        self, live_client, pre_fix_behavior,
    ):
        resp, latencies = _run_while_polling(
            live_client, lambda: _chat(live_client, stream=False))
        assert resp.status_code == 200
        assert max(latencies) >= SLEEP_S - 1.0
