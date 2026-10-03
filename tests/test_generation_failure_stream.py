"""
tests/test_generation_failure_stream.py

A generation failure inside the SSE generator must reach the client as a typed
error frame (ADR-040 v3), not as HTTP 200 with an empty body. Headers are
flushed before the generator runs, so the frame is the only channel.

Every absence assertion here ("no vault_sources frame", "no vault header")
has a positive control in the same file: the identical request with a
successful generation, where the thing does occur.

Only the buffer-then-stream path is covered. `_needs_grounding` is the
constant True in openai_adapter, so the raw fast-streaming branch is not
reachable and is not wrapped.
"""

import json
import logging
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.context.models import ContextItem, ContextPacket
from src.llm.adapter import StatusSignal

ERROR_MESSAGE = "Ember couldn't generate a reply. Check that the model server is reachable."
MARKER = "EXC-MARKER-7f3a"


def _packet() -> ContextPacket:
    item = ContextItem(
        id="fixture-1",
        content="A synthetic fixture record with enough content to pass filters.",
        source="conversation",
        item_type="conversation",
        memory_type="conversation",
        score=0.6,
        timestamp="2026-03-15T10-00-00",
    )
    return ContextPacket(user_message="hello there", memory_items=[item])


def _events(text: str) -> list:
    out: list = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        out.append("[DONE]" if payload == "[DONE]" else json.loads(payload))
    return out


def _types(events: list) -> list:
    return [e.get("type") for e in events if isinstance(e, dict) and "type" in e]


@pytest.fixture
def client():
    with patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        yield TestClient(app)


def _post(client, *, stream: bool, iter_side_effect=None, iter_items=None):
    """POST a chat turn with generation stubbed; returns the response."""
    from src.api import openai_adapter as oa

    gen_patch = patch.object(
        oa.llm_adapter,
        "generate_response_iter",
        side_effect=iter_side_effect,
        return_value=iter(iter_items or []),
    )
    with patch.object(oa.context_service, "build_context", return_value=_packet()), \
         gen_patch, \
         patch.object(oa.llm_adapter, "generate_response", return_value="A grounded reply."), \
         patch("src.safety.grounding_check.run_grounding_check", return_value=(True, None)), \
         patch("src.api.openai_adapter.write_memory"), \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as onb, \
         patch("src.api.openai_adapter._ensure_session"):
        onb.is_active.return_value = False
        return client.post(
            "/v1/chat/completions",
            json={
                "model": "ember-2",
                "messages": [{"role": "user", "content": "hello there"}],
                "stream": stream,
            },
            headers={"X-Test-Session": "true"},
        )


def _raise(*_a, **_k):
    raise RuntimeError(MARKER)


def test_failure_emits_error_frame_then_stop_and_done(client):
    resp = _post(client, stream=True, iter_side_effect=_raise)
    events = _events(resp.text)

    assert resp.status_code == 200  # headers flushed before the body ran
    errors = [e for e in events if isinstance(e, dict) and e.get("type") == "error"]
    assert errors == [
        {"type": "error", "code": "generation_failed", "message": ERROR_MESSAGE}
    ]
    stop = events[-2]
    assert stop["choices"][0]["finish_reason"] == "stop"
    assert events[-1] == "[DONE]"
    assert events.index(errors[0]) < len(events) - 2


def test_failure_emits_no_sources_frames(client):
    resp = _post(client, stream=True, iter_side_effect=_raise)
    types = _types(_events(resp.text))
    assert "vault_sources" not in types
    assert "sources" not in types


def test_success_control_emits_vault_sources_and_no_error(client):
    resp = _post(client, stream=True, iter_items=["A grounded reply."])
    types = _types(_events(resp.text))
    assert "vault_sources" in types
    assert "error" not in types


def test_exception_text_never_reaches_the_wire(client):
    resp = _post(client, stream=True, iter_side_effect=_raise)
    assert MARKER not in resp.text


def test_exception_is_logged_with_type_only_in_message(client, caplog):
    with caplog.at_level(logging.ERROR):
        _post(client, stream=True, iter_side_effect=_raise)
    records = [r for r in caplog.records if r.getMessage().startswith("[GENERATION] failed")]
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].getMessage() == "[GENERATION] failed: RuntimeError"
    assert records[0].exc_info is not None


def test_failure_after_status_frame_keeps_status_then_errors(client):
    def _status_then_raise(*_a, **_k):
        yield StatusSignal("review_pending")
        raise RuntimeError(MARKER)

    resp = _post(client, stream=True, iter_side_effect=_status_then_raise)
    types = _types(_events(resp.text))
    assert "status" in types
    assert types.index("status") < types.index("error")
    assert "vault_sources" not in types


def test_streaming_response_has_no_vault_header(client):
    resp = _post(client, stream=True, iter_items=["A grounded reply."])
    assert "x-ember-vault-used" not in resp.headers


def test_non_streaming_control_still_sets_vault_header(client):
    resp = _post(client, stream=False)
    assert resp.status_code == 200
    assert resp.headers.get("x-ember-vault-used") == "true"


# ---------------------------------------------------------------------------
# End to end: real adapter, real prompt build, unreachable generation host.
# Auxiliary callers stay on the (mocked) local ollama module, matching the
# EMBER_GENERATION_OLLAMA_HOST split in src/core/config.py.
# ---------------------------------------------------------------------------

UNREACHABLE_HOST = "127.0.0.1:1"


def _local_chat(**kwargs):
    if kwargs.get("stream"):
        return iter([{"message": {"content": "A plain reply."}}])
    return {"message": {"content": "A plain reply."}}


def _post_real_adapter(client, committed: list):
    from src.api import openai_adapter as oa

    packet = _packet()
    packet.arm_delivery_recorder(lambda items: committed.append(list(items)))
    with patch.object(oa.context_service, "build_context", return_value=packet), \
         patch("ollama.chat", side_effect=_local_chat), \
         patch("ollama.embed", return_value={"embeddings": [[0.0] * 768]}), \
         patch("src.safety.grounding_check.run_grounding_check", return_value=(True, None)), \
         patch("src.api.openai_adapter.write_memory"), \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as onb, \
         patch("src.api.openai_adapter._ensure_session"):
        onb.is_active.return_value = False
        return client.post(
            "/v1/chat/completions",
            json={
                "model": "ember-2",
                "messages": [{"role": "user", "content": "hello there"}],
                "stream": True,
            },
            headers={"X-Test-Session": "true"},
        )


def test_unreachable_generation_host_surfaces_error_and_skips_commit(client, monkeypatch, caplog):
    monkeypatch.setenv("EMBER_GENERATION_OLLAMA_HOST", UNREACHABLE_HOST)
    committed: list = []
    with caplog.at_level(logging.ERROR):
        resp = _post_real_adapter(client, committed)
    events = _events(resp.text)
    types = _types(events)

    assert resp.status_code == 200
    assert "error" in types
    assert "vault_sources" not in types
    assert "x-ember-vault-used" not in resp.headers
    assert events[-1] == "[DONE]"
    assert committed == []
    assert any(r.getMessage().startswith("[GENERATION] failed") for r in caplog.records)


def test_reachable_generation_control_delivers_and_commits(client, monkeypatch):
    monkeypatch.delenv("EMBER_GENERATION_OLLAMA_HOST", raising=False)
    committed: list = []
    resp = _post_real_adapter(client, committed)
    types = _types(_events(resp.text))

    assert "error" not in types
    assert "vault_sources" in types
    assert len(committed) == 1
