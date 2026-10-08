"""
tests/test_sse_guard.py

guard_sse() (src/api/sse.py) unit behavior, plus the early-return
StreamingResponse site in src/api/openai_adapter.py. The chat_completions
site and its per-stage raises are covered in
tests/test_generation_failure_stream.py.

Every "no duplicate" / "nothing added" assertion has a control in this file
where the guard does add the terminal frames.
"""

import asyncio
import json
import logging
from unittest.mock import patch

import pytest

from src.api.sse import (
    GENERATION_FAILED_CODE,
    guard_sse,
    sse_chunk,
    sse_done,
    sse_error,
)

CID = "chatcmpl-test-guard"
MARKER = "GUARD-MARKER-5d10"


def _collect(agen) -> list[str]:
    async def _run():
        return [frame async for frame in agen]
    return asyncio.run(_run())


def _events(frames: list[str]) -> list:
    out: list = []
    for line in "".join(frames).splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        out.append("[DONE]" if payload == "[DONE]" else json.loads(payload))
    return out


def _error_count(events) -> int:
    return sum(1 for e in events if isinstance(e, dict) and e.get("type") == "error")


def _stop_count(events) -> int:
    return sum(
        1 for e in events
        if isinstance(e, dict) and e.get("choices")
        and e["choices"][0].get("finish_reason") == "stop"
    )


def _assert_failed_once(events):
    assert _error_count(events) == 1
    assert _stop_count(events) == 1
    assert events.count("[DONE]") == 1
    assert events[-1] == "[DONE]"
    assert events[-3]["code"] == GENERATION_FAILED_CODE


# --- async bodies ----------------------------------------------------------

def test_async_body_raise_before_any_frame():
    async def body():
        raise RuntimeError(MARKER)
        yield  # pragma: no cover - makes this a generator

    frames = _collect(guard_sse(body(), CID))
    _assert_failed_once(_events(frames))
    assert MARKER not in "".join(frames)


def test_async_body_raise_mid_stream_keeps_earlier_frames():
    async def body():
        yield sse_chunk(CID, content="partial ")
        raise RuntimeError(MARKER)

    events = _events(_collect(guard_sse(body(), CID)))
    assert events[0]["choices"][0]["delta"]["content"] == "partial "
    _assert_failed_once(events)


def test_async_body_no_exception_control_passes_through_unchanged():
    frames_in = [sse_chunk(CID, content="hello"), sse_chunk(CID, finish_reason="stop") + sse_done()]

    async def body():
        for f in frames_in:
            yield f

    assert _collect(guard_sse(body(), CID)) == frames_in


def test_async_body_raise_after_done_adds_nothing(caplog):
    async def body():
        yield sse_chunk(CID, finish_reason="stop") + sse_done()
        raise RuntimeError(MARKER)

    with caplog.at_level(logging.ERROR):
        events = _events(_collect(guard_sse(body(), CID)))
    assert _error_count(events) == 0
    assert events.count("[DONE]") == 1
    messages = [r.getMessage() for r in caplog.records]
    assert "[SSE] post-terminal failure: RuntimeError" in messages  # control: it raised


def test_body_that_emitted_its_own_error_and_done_gets_no_duplicate():
    async def body():
        yield sse_error() + sse_chunk(CID, finish_reason="stop") + sse_done()
        raise RuntimeError(MARKER)

    events = _events(_collect(guard_sse(body(), CID)))
    _assert_failed_once(events)  # exactly one of each, from the body


# --- sync bodies (fast-streaming path) -------------------------------------

def test_sync_body_raise_mid_stream():
    def body():
        yield sse_chunk(CID, content="partial ")
        raise RuntimeError(MARKER)

    events = _events(_collect(guard_sse(body(), CID)))
    assert events[0]["choices"][0]["delta"]["content"] == "partial "
    _assert_failed_once(events)


def test_sync_body_no_exception_control():
    def body():
        yield sse_chunk(CID, content="hello")
        yield sse_chunk(CID, finish_reason="stop") + sse_done()

    events = _events(_collect(guard_sse(body(), CID)))
    assert _error_count(events) == 0
    assert events.count("[DONE]") == 1


# --- cancellation is not swallowed -----------------------------------------

def test_cancellation_propagates_and_closes_the_body():
    closed = []

    async def body():
        try:
            yield sse_chunk(CID, content="one")
            await asyncio.sleep(10)
            yield sse_chunk(CID, content="two")  # pragma: no cover
        finally:
            closed.append(True)

    async def _run():
        agen = guard_sse(body(), CID)
        first = await agen.__anext__()
        task = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return first

    first = asyncio.run(_run())
    assert "one" in first
    assert closed == [True]


def test_generator_exit_closes_inner_body():
    closed = []

    async def body():
        try:
            yield sse_chunk(CID, content="one")
            yield sse_chunk(CID, content="two")  # pragma: no cover
        finally:
            closed.append(True)

    async def _run():
        agen = guard_sse(body(), CID)
        await agen.__anext__()
        await agen.aclose()

    asyncio.run(_run())
    assert closed == [True]


# --- the early-return StreamingResponse site --------------------------------

def _drain_response(resp) -> list[str]:
    async def _run():
        return [f if isinstance(f, str) else f.decode() async for f in resp.body_iterator]
    return asyncio.run(_run())


def test_early_return_stream_raise_emits_terminal_frames_once():
    from src.api import openai_adapter as oa

    real_chunk = oa.sse_chunk
    calls = {"n": 0}

    def _chunk_then_raise(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:  # the early-return stop chunk
            raise RuntimeError(MARKER)
        return real_chunk(*a, **k)

    with patch.object(oa, "sse_chunk", side_effect=_chunk_then_raise):
        resp = oa.early_return_response("A canned reply.", CID, stream=True, label="test")
        frames = _drain_response(resp)
    events = _events(frames)
    assert events[0]["choices"][0]["delta"]["content"] == "A canned reply."
    _assert_failed_once(events)
    assert MARKER not in "".join(frames)


def test_early_return_stream_no_exception_control():
    from src.api import openai_adapter as oa

    resp = oa.early_return_response("A canned reply.", CID, stream=True, label="test")
    events = _events(_drain_response(resp))
    assert _error_count(events) == 0
    assert _stop_count(events) == 1
    assert events.count("[DONE]") == 1
