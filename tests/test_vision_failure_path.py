"""
tests/test_vision_failure_path.py

Regression tests for the vision preprocessing FAILURE path at
src/api/openai_adapter.py.

Found in UAT 2026-07-26 (issue #130): when the VL preprocessor could not
load its model, the failure was caught non-fatal and the turn continued
with image_data still on the context packet. The raw images were then
forwarded to a text-only chat model, Ollama rejected the call with
400 "model does not support multimodal requests", and that exception
escaped the ASGI handler AFTER the 200 and the source badges had already
been written to the stream. The client was left holding an open stream
that never received content: badges present, response body empty.

Coverage before this file stopped at the service boundary
(test_vision_pipeline.py asserts analyze() returns empty on failure;
test_vision_service_logging.py asserts the failure event is logged).
Nothing asserted what the adapter does next, which is exactly where the
defect lived.

These tests drive the real chat completions endpoint with a multimodal
payload and a vision_service that raises.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# A non-empty base64 string is sufficient. vision_service.analyze is mocked
# in every test, so the bytes are never decoded.
_FAKE_IMAGE_DATA_URL = "data:image/png;base64,aGVsbG8="


def _multimodal_payload(text: str = "what is in this image", *, stream: bool = False) -> dict:
    return {
        "model": "ember",
        "stream": stream,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": text},
                    {"type": "image_url", "image_url": {"url": _FAKE_IMAGE_DATA_URL}},
                ],
            }
        ],
    }


@pytest.fixture
def client():
    with patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        yield TestClient(app)


def test_vision_failure_does_not_forward_images_to_text_model(client):
    """When the VL preprocessor raises, the turn must not reach the chat
    model at all.

    The chat model is text-only in every shipped configuration, so
    forwarding raw images to it is what produced the Ollama 400 and the
    escaped exception. Short-circuiting before generation is what makes
    that structurally impossible rather than merely unlikely.
    """
    from src.context.models import ContextPacket

    empty_packet = ContextPacket(user_message="what is in this image", web_items=[])

    with patch("src.api.openai_adapter.context_service") as _ctx, \
         patch("src.api.openai_adapter.llm_adapter") as _llm, \
         patch("src.api.openai_adapter.vision_service") as _vision, \
         patch("src.api.openai_adapter.write_memory"), \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as _onb, \
         patch("src.api.openai_adapter._ensure_session"), \
         patch("src.core.preferences.get", return_value=False):
        _onb.is_active.return_value = False
        _ctx.build_context.return_value = empty_packet
        # The exact failure observed in UAT: model load fails inside analyze().
        _vision.analyze.side_effect = RuntimeError(
            "llama-server process has terminated: unknown model architecture"
        )
        _llm.generate_response.return_value = "should never be produced"

        resp = client.post("/v1/chat/completions", json=_multimodal_payload())

        assert resp.status_code == 200
        # The load-bearing assertion: generation never ran, so no raw image
        # bytes could reach a text-only model.
        assert _llm.generate_response.call_count == 0


def test_vision_failure_returns_sse_when_streaming(client):
    """A streaming client must receive SSE carrying the message, never JSON.

    This is the assertion that maps directly to the reported symptom.
    CLAUDE.md Bug Standard #1: any early-return path in the streaming
    endpoint must return a StreamingResponse when stream=True. Returning
    JSON (or raising) renders as a blank reply with no surfaced error,
    which is exactly what UAT saw.
    """
    from src.context.models import ContextPacket
    from src.llm.vision_service import VISION_UNAVAILABLE_RESPONSE

    empty_packet = ContextPacket(user_message="what is in this image", web_items=[])

    with patch("src.api.openai_adapter.context_service") as _ctx, \
         patch("src.api.openai_adapter.llm_adapter") as _llm, \
         patch("src.api.openai_adapter.vision_service") as _vision, \
         patch("src.api.openai_adapter.write_memory"), \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as _onb, \
         patch("src.api.openai_adapter._ensure_session"), \
         patch("src.core.preferences.get", return_value=False):
        _onb.is_active.return_value = False
        _ctx.build_context.return_value = empty_packet
        _vision.analyze.side_effect = RuntimeError("model load failed")
        _llm.generate_response.return_value = "should never be produced"

        resp = client.post(
            "/v1/chat/completions", json=_multimodal_payload(stream=True)
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = resp.text
        # The message reaches the client, and the stream terminates properly
        # instead of hanging open with no content.
        assert VISION_UNAVAILABLE_RESPONSE[:30] in body
        assert "[DONE]" in body
        assert _llm.generate_response.call_count == 0


def test_vision_success_path_still_generates(client):
    """Guard against over-correcting: a successful preprocess must still
    reach generation, with the raw bytes stripped from the packet.

    Without this, a fix that short-circuits too eagerly would disable
    vision entirely and still pass the failure-path tests above.
    """
    from src.context.models import ContextPacket

    packet = ContextPacket(user_message="what is in this image", web_items=[])

    with patch("src.api.openai_adapter.context_service") as _ctx, \
         patch("src.api.openai_adapter.llm_adapter") as _llm, \
         patch("src.api.openai_adapter.vision_service") as _vision, \
         patch("src.api.openai_adapter.write_memory"), \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as _onb, \
         patch("src.api.openai_adapter._ensure_session"), \
         patch("src.core.preferences.get", return_value=False):
        _onb.is_active.return_value = False
        _ctx.build_context.return_value = packet
        _vision.analyze.return_value = "A test image of a cat."
        _llm.generate_response.return_value = "I see a cat."

        resp = client.post("/v1/chat/completions", json=_multimodal_payload())

        assert resp.status_code == 200
        assert _llm.generate_response.call_count == 1
        # ADR-032: only the VL preprocessor sees raw bytes.
        assert packet.image_data == []
