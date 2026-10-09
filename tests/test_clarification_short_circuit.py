"""tests/test_clarification_short_circuit.py

B2 fix: when a bare-marker query like "google please" reaches the
chat completions endpoint, the request short-circuits at the policy
layer. The user receives SCRIPTED_CLARIFICATION_RESPONSE directly
without context build, retrieval, LLM generation, or constitutional
review.

Both conversation records (user turn + assistant turn) are still
written so the next-turn handler can detect the awaiting_search_content
flag on the assistant turn.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.context.policies import SCRIPTED_CLARIFICATION_RESPONSE


@pytest.fixture
def client():
    with patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        yield TestClient(app)


def _bare_marker_payload(text: str = "google please") -> dict:
    return {
        "model": "ember",
        "stream": False,
        "messages": [{"role": "user", "content": text}],
    }


def test_bare_marker_returns_scripted_clarification(client):
    """The clarification text reaches the client as the assistant's
    response content."""
    with patch("src.api.openai_adapter.context_service") as _ctx, \
         patch("src.api.openai_adapter.llm_adapter") as _llm, \
         patch("src.api.openai_adapter.ExchangeRecorder"), \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as _onb, \
         patch("src.api.openai_adapter._background_topic_decline_resolution"), \
         patch("src.tools.web_search.web_search") as _web:
        _onb.is_active.return_value = False

        resp = client.post("/v1/chat/completions", json=_bare_marker_payload())

        assert resp.status_code == 200
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        assert content == SCRIPTED_CLARIFICATION_RESPONSE

        # No LLM call, no context build, no web search dispatched.
        assert _ctx.build_context.call_count == 0
        assert _llm.generate_response.call_count == 0
        assert _web.call_count == 0


def test_bare_marker_writes_both_conversation_records_with_metadata(client):
    """User turn and assistant turn must both be written. The user turn is
    stored in Phase A (ADR-047); the assistant record carries
    metadata.source='clarification' and metadata.awaiting_search_content=True
    so the next-turn handler can detect the followup."""
    with patch("src.api.openai_adapter.context_service"), \
         patch("src.api.openai_adapter.llm_adapter"), \
         patch("src.api.openai_adapter.ExchangeRecorder") as _recorder_cls, \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as _onb, \
         patch("src.api.openai_adapter._background_topic_decline_resolution"), \
         patch("src.tools.web_search.web_search"):
        _onb.is_active.return_value = False

        resp = client.post("/v1/chat/completions", json=_bare_marker_payload())

        assert resp.status_code == 200
        recorder = _recorder_cls.return_value
        # User turn: the text exactly as sent, written once, in Phase A.
        recorder.record_user_turn.assert_called_once_with("google please", 0)

        # Assistant turn: clarification text + clarification-source flags.
        recorder.record_reply.assert_called_once()
        args, kwargs = recorder.record_reply.call_args
        assert args == (SCRIPTED_CLARIFICATION_RESPONSE,)
        assert kwargs["metadata"]["source"] == "clarification"
        assert kwargs["metadata"]["awaiting_search_content"] is True
        assert "clarification" in kwargs["tags"]


def test_bare_marker_streaming_emits_clarification_sse(client):
    """When body.stream=True, the response is an SSE stream containing
    the clarification text and a [DONE] sentinel. No LLM is invoked."""
    payload = _bare_marker_payload()
    payload["stream"] = True
    with patch("src.api.openai_adapter.context_service") as _ctx, \
         patch("src.api.openai_adapter.llm_adapter") as _llm, \
         patch("src.api.openai_adapter.ExchangeRecorder"), \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as _onb, \
         patch("src.api.openai_adapter._background_topic_decline_resolution"), \
         patch("src.tools.web_search.web_search"):
        _onb.is_active.return_value = False

        resp = client.post("/v1/chat/completions", json=payload)

        assert resp.status_code == 200
        body = resp.text
        assert SCRIPTED_CLARIFICATION_RESPONSE in body
        assert "data: [DONE]" in body
        assert _llm.generate_response.call_count == 0
        assert _ctx.build_context.call_count == 0
