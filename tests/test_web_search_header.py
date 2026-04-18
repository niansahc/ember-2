"""
tests/test_web_search_header.py

Tests for the X-Web-Search response header (web search transparency).
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with mocked dependencies."""
    with patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        yield TestClient(app)


class TestWebSearchHeader:
    """Verify X-Web-Search header appears when web items are used."""

    def test_header_present_when_web_search_used(self, client):
        """X-Web-Search: true should appear when context packet has web_items."""
        from src.context.models import ContextPacket

        mock_packet = ContextPacket(
            user_message="what is the weather today",
            web_items=[{"title": "Weather", "url": "http://example.com", "snippet": "Sunny"}],
        )

        with patch("src.api.openai_adapter.context_service") as mock_ctx, \
             patch("src.api.openai_adapter.llm_adapter") as mock_llm, \
             patch("src.api.openai_adapter.write_memory"), \
             patch("src.api.openai_adapter._background_state_extraction"), \
             patch("src.api.openai_adapter._detect_and_write_commitment"), \
             patch("src.api.openai_adapter._detect_task_in_response"), \
             patch("src.api.openai_adapter.onboarding_service") as mock_onb, \
             patch("src.api.openai_adapter._ensure_session"), \
             patch("src.core.preferences.get", return_value=True):
            mock_onb.is_active.return_value = False
            mock_ctx.build_context.return_value = mock_packet
            mock_llm.generate_response.return_value = "Based on current search results, it's sunny today."

            resp = client.post("/v1/chat/completions", json={
                "model": "ember",
                "messages": [{"role": "user", "content": "what is the weather today"}],
                "stream": False,
            })

            assert resp.status_code == 200
            assert resp.headers.get("x-ember-web-search") == "true"

    def test_header_absent_when_no_web_search(self, client):
        """X-Web-Search header should not appear when no web items used."""
        from src.context.models import ContextPacket

        mock_packet = ContextPacket(
            user_message="tell me about myself",
            web_items=[],
        )

        with patch("src.api.openai_adapter.context_service") as mock_ctx, \
             patch("src.api.openai_adapter.llm_adapter") as mock_llm, \
             patch("src.api.openai_adapter.write_memory"), \
             patch("src.api.openai_adapter._background_state_extraction"), \
             patch("src.api.openai_adapter._detect_and_write_commitment"), \
             patch("src.api.openai_adapter._detect_task_in_response"), \
             patch("src.api.openai_adapter.onboarding_service") as mock_onb, \
             patch("src.api.openai_adapter._ensure_session"):
            mock_onb.is_active.return_value = False
            mock_ctx.build_context.return_value = mock_packet
            mock_llm.generate_response.return_value = "You're Chas."

            resp = client.post("/v1/chat/completions", json={
                "model": "ember",
                "messages": [{"role": "user", "content": "tell me about myself"}],
                "stream": False,
            })

            assert resp.status_code == 200
            assert "x-ember-web-search" not in resp.headers
