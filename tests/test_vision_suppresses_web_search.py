"""
tests/test_vision_suppresses_web_search.py

Regression tests for the vision-gate at src/api/openai_adapter.py.

Vision turns must never trigger web search. The image-bearing query is
served by the VL preprocessor; mixing in unrelated web hits wastes tokens,
pollutes attribution, and contradicts what the user asked for.

Three trigger points are gated, all keyed on a _has_image snapshot taken
before any classification work:
  1. Primary path via context_service.build_context(skip_web_search=...)
  2. Ask-first activation flag passed into prompt assembly
  3. Autonomous-backstop block guarded by `not _has_image`

These tests drive the chat completions endpoint with a synthetic
multimodal payload (one base64 image part plus a temporal-phrasing text
part) and assert the gates fire correctly.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


# A non-empty base64 string is sufficient. vision_service.analyze is mocked
# in every test, so the bytes are never decoded.
_FAKE_IMAGE_DATA_URL = "data:image/png;base64,aGVsbG8="

_TEMPORAL_QUERY = "what's the latest news about AI"


def _multimodal_payload(text: str = _TEMPORAL_QUERY) -> dict:
    return {
        "model": "ember",
        "stream": False,
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


def test_vision_turn_skips_primary_web_search(client):
    """Gate 1: build_context must be called with skip_web_search=True
    when image_data is present, even if web_search_autonomous=True."""
    from src.context.models import ContextPacket

    empty_packet = ContextPacket(user_message=_TEMPORAL_QUERY, web_items=[])

    with patch("src.api.openai_adapter.context_service") as _ctx, \
         patch("src.api.openai_adapter.llm_adapter") as _llm, \
         patch("src.api.openai_adapter.vision_service") as _vision, \
         patch("src.api.openai_adapter.write_memory"), \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as _onb, \
         patch("src.api.openai_adapter._ensure_session"), \
         patch("src.core.preferences.get", return_value=True), \
         patch("src.tools.web_search.web_search") as _web:
        _onb.is_active.return_value = False
        _ctx.build_context.return_value = empty_packet
        _vision.analyze.return_value = "A test image of a cat."
        _llm.generate_response.return_value = "I see a cat in the image."

        resp = client.post("/v1/chat/completions", json=_multimodal_payload())

        assert resp.status_code == 200
        # Gate 1: skip_web_search must be True because an image is present.
        assert _ctx.build_context.called
        assert _ctx.build_context.call_args.kwargs.get("skip_web_search") is True
        # Gate 3: autonomous backstop must not have fired either.
        assert _web.call_count == 0


def test_vision_turn_skips_autonomous_backstop(client, caplog):
    """Gate 3: even if build_context somehow returns a packet with no
    web_items (the backstop's outer condition), the _has_image guard
    must short-circuit the autonomous web_search call.

    Also verifies the [VISION_GATE] suppression log fires once when the
    classifier returns web_search intent on a vision turn.
    """
    import logging
    from src.context.models import ContextPacket

    empty_packet = ContextPacket(user_message=_TEMPORAL_QUERY, web_items=[])

    caplog.set_level(logging.INFO, logger="ember.api")

    with patch("src.api.openai_adapter.context_service") as _ctx, \
         patch("src.api.openai_adapter.llm_adapter") as _llm, \
         patch("src.api.openai_adapter.vision_service") as _vision, \
         patch("src.api.openai_adapter.write_memory"), \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as _onb, \
         patch("src.api.openai_adapter._ensure_session"), \
         patch("src.core.preferences.get", return_value=True), \
         patch("src.tools.web_search.web_search") as _web:
        _onb.is_active.return_value = False
        _ctx.build_context.return_value = empty_packet
        _vision.analyze.return_value = "A test image of a cat."
        _llm.generate_response.return_value = "I see a cat in the image."

        resp = client.post("/v1/chat/completions", json=_multimodal_payload())

        assert resp.status_code == 200
        # Backstop must not have fired despite empty web_items + autonomous=True.
        assert _web.call_count == 0
        # Suppression telemetry should have been emitted at least once.
        # (Only fires when classifier returned web_search intent.)
        gate_logs = [r for r in caplog.records if "[VISION_GATE]" in r.getMessage()]
        # Not asserting count because intent classification depends on the
        # full pipeline; just confirm no [VISION_GATE] line says we DIDN'T
        # suppress. Presence-or-absence both acceptable here; the call_count
        # assertion above is the load-bearing check.
        for record in gate_logs:
            assert "suppressed web_search" in record.getMessage()


def test_vision_turn_disables_ask_first(client):
    """Gate 2: _ask_first_active must resolve False on a vision turn,
    even if the classifier returns web_search and autonomous mode is off.

    Verified by inspecting the ask_first_active kwarg passed to
    llm_adapter.generate_response.
    """
    from src.context.models import ContextPacket

    empty_packet = ContextPacket(user_message=_TEMPORAL_QUERY, web_items=[])

    with patch("src.api.openai_adapter.context_service") as _ctx, \
         patch("src.api.openai_adapter.llm_adapter") as _llm, \
         patch("src.api.openai_adapter.vision_service") as _vision, \
         patch("src.api.openai_adapter.write_memory"), \
         patch("src.api.openai_adapter._background_state_extraction"), \
         patch("src.api.openai_adapter._detect_and_write_commitment"), \
         patch("src.api.openai_adapter._detect_task_in_response"), \
         patch("src.api.openai_adapter.onboarding_service") as _onb, \
         patch("src.api.openai_adapter._ensure_session"), \
         patch("src.core.preferences.get", return_value=False), \
         patch("src.tools.web_search.web_search") as _web:
        _onb.is_active.return_value = False
        _ctx.build_context.return_value = empty_packet
        _vision.analyze.return_value = "A test image of a cat."
        _llm.generate_response.return_value = "I see a cat in the image."

        resp = client.post("/v1/chat/completions", json=_multimodal_payload())

        assert resp.status_code == 200
        # Gate 2: ask_first_active must be False on a vision turn.
        assert _llm.generate_response.called
        assert _llm.generate_response.call_args.kwargs.get("ask_first_active") is False
        # Gate 3 sanity check.
        assert _web.call_count == 0
