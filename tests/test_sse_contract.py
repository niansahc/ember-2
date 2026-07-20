"""
tests/test_sse_contract.py

Golden-frame tests for the SSE wire contract (ADR-040). These pin the exact
bytes the backend serializer emits so the contract cannot drift without a test
failure. The `created` field is a wall-clock int and is the only non-frozen
field; everything else (keys, order, nesting, delta variants, type values) is
asserted exactly.

Contract v2 (B-SSE-001, ADR-040): status is a top-level typed frame
{"type": "status", "content": "<value>"} -- a sibling of the sources /
vault_sources frames -- NOT a choices[0].delta.status chunk. The v1 delta.status
shape was silently dropped by the UI parser (which reads a top-level frame), so
no working consumer was broken by the move. These assertions are pinned to v2;
any further shape change must follow the ADR-040 change procedure.
"""

import json

from src.api.sse import (
    sse_chunk,
    sse_done,
    sse_sources,
    sse_vault_sources,
)

# Every status value the backend emits today (openai_adapter _status_event
# calls + StatusSignal names from adapter.py, per ADR-040).
STATUS_VALUES = ["searching", "review_pending", "review_complete", "verifying", "refining"]

_ID = "chatcmpl-golden"


def _payload(frame: str) -> dict:
    """Parse the single `data: {...}\\n\\n` JSON frame into a dict."""
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    return json.loads(frame[len("data: "):].strip())


def test_content_frame_exact_bytes():
    frame = sse_chunk(_ID, content="hi there")
    # Byte-for-byte around the non-deterministic created int.
    assert frame.startswith(
        'data: {"id": "chatcmpl-golden", "object": "chat.completion.chunk", "created": '
    )
    assert frame.endswith(
        ', "model": "ember-2", "choices": [{"index": 0, "delta": {"content": "hi there"}, "finish_reason": null}]}\n\n'
    )
    p = _payload(frame)
    assert isinstance(p["created"], int)
    assert p["object"] == "chat.completion.chunk"
    assert p["model"] == "ember-2"
    assert p["choices"][0]["delta"] == {"content": "hi there"}
    assert p["choices"][0]["finish_reason"] is None


def test_initial_typing_indicator_is_empty_content():
    p = _payload(sse_chunk(_ID, content=""))
    assert p["choices"][0]["delta"] == {"content": ""}
    assert p["choices"][0]["finish_reason"] is None


def test_status_frames_are_top_level_typed_for_every_value():
    # ADR-040 v2 (B-SSE-001): status is a top-level typed frame carrying the
    # phase in `content` (the key the UI parser already reads), NOT a
    # chat.completion.chunk delta.
    from src.api.sse import sse_status

    for value in STATUS_VALUES:
        frame = sse_status(value)
        assert frame == 'data: {"type": "status", "content": "%s"}\n\n' % value
        p = _payload(frame)
        assert p == {"type": "status", "content": value}
        # No OpenAI chunk envelope for status frames anymore.
        assert "choices" not in p
        assert "object" not in p


def test_terminal_stop_frame():
    frame = sse_chunk(_ID, finish_reason="stop")
    assert frame.endswith(
        ', "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}\n\n'
    )
    p = _payload(frame)
    assert p["choices"][0]["delta"] == {}
    assert p["choices"][0]["finish_reason"] == "stop"


def test_done_terminator():
    assert sse_done() == "data: [DONE]\n\n"


def test_sources_frame():
    frame = sse_sources([{"title": "T", "url": "https://example.com"}])
    assert frame == (
        'data: {"type": "sources", "sources": [{"title": "T", "url": "https://example.com"}]}\n\n'
    )


def test_vault_sources_frame():
    frame = sse_vault_sources([{"type": "state", "timestamp": "2026-01-01T00-00-00", "summary": "s"}])
    assert frame == (
        'data: {"type": "vault_sources", "sources": [{"type": "state", "timestamp": "2026-01-01T00-00-00", "summary": "s"}]}\n\n'
    )


def test_empty_message_stream_matches_serializer(monkeypatch):
    """Integration: the live empty-message early-return stream is built from the
    same serializer (content frame -> stop frame -> [DONE])."""
    from unittest.mock import patch
    from fastapi.testclient import TestClient

    with patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        client = TestClient(app)
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "ember-2", "messages": [{"role": "user", "content": "  "}], "stream": True},
            headers={"X-Test-Session": "true"},
        )
    assert resp.headers["content-type"].startswith("text/event-stream")
    lines = [ln for ln in resp.text.split("\n\n") if ln.startswith("data: ")]
    # content frame, stop frame, [DONE]
    first = json.loads(lines[0][len("data: "):])
    assert first["object"] == "chat.completion.chunk"
    assert "content" in first["choices"][0]["delta"]
    stop = json.loads(lines[1][len("data: "):])
    assert stop["choices"][0]["finish_reason"] == "stop"
    assert lines[2] == "data: [DONE]"
