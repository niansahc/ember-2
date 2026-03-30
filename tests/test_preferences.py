"""
tests/test_preferences.py

Tests for user preferences store and conversational style prompt injection.
"""

import json
import pytest
from unittest.mock import patch

from src.core import preferences


class TestPreferencesStore:
    """Unit tests for src.core.preferences."""

    def test_read_empty_vault(self, tmp_path):
        result = preferences.read(vault_path=tmp_path)
        assert result == {}

    def test_write_and_read(self, tmp_path):
        preferences.write("conversational_style", "casual", vault_path=tmp_path)
        result = preferences.read(vault_path=tmp_path)
        assert result == {"conversational_style": "casual"}

    def test_write_multiple_keys(self, tmp_path):
        preferences.write("conversational_style", "thoughtful", vault_path=tmp_path)
        preferences.write("theme", "dark", vault_path=tmp_path)
        result = preferences.read(vault_path=tmp_path)
        assert result["conversational_style"] == "thoughtful"
        assert result["theme"] == "dark"

    def test_get_with_default(self, tmp_path):
        result = preferences.get("nonexistent", "fallback", vault_path=tmp_path)
        assert result == "fallback"

    def test_get_existing_key(self, tmp_path):
        preferences.write("style", "casual", vault_path=tmp_path)
        result = preferences.get("style", vault_path=tmp_path)
        assert result == "casual"

    def test_update_multiple(self, tmp_path):
        preferences.update({"a": 1, "b": 2}, vault_path=tmp_path)
        result = preferences.read(vault_path=tmp_path)
        assert result == {"a": 1, "b": 2}

    def test_handles_corrupted_file(self, tmp_path):
        prefs_path = tmp_path / "preferences.json"
        prefs_path.write_text("not json!", encoding="utf-8")
        result = preferences.read(vault_path=tmp_path)
        assert result == {}

    def test_overwrites_existing_key(self, tmp_path):
        preferences.write("style", "casual", vault_path=tmp_path)
        preferences.write("style", "thoughtful", vault_path=tmp_path)
        assert preferences.get("style", vault_path=tmp_path) == "thoughtful"


class TestStylePromptInjection:
    """Unit tests for conversational style in PromptBuilder."""

    @pytest.fixture
    def builder(self):
        with patch("src.llm.prompt_builder.Path.read_text", return_value="You are Ember."):
            from src.llm.prompt_builder import PromptBuilder
            return PromptBuilder()

    def test_casual_injects_instruction(self, builder):
        from src.context.models import ContextPacket
        packet = ContextPacket(user_message="hello")
        prompt = builder.build_prompt(packet, style="casual")
        assert "CONVERSATIONAL STYLE: CASUAL" in prompt
        assert "concise and informal" in prompt

    def test_thoughtful_injects_instruction(self, builder):
        from src.context.models import ContextPacket
        packet = ContextPacket(user_message="hello")
        prompt = builder.build_prompt(packet, style="thoughtful")
        assert "CONVERSATIONAL STYLE: THOUGHTFUL" in prompt
        assert "depth and clarity" in prompt

    def test_balanced_injects_nothing(self, builder):
        from src.context.models import ContextPacket
        packet = ContextPacket(user_message="hello")
        prompt = builder.build_prompt(packet, style="balanced")
        assert "CONVERSATIONAL STYLE" not in prompt

    def test_unknown_falls_back_to_balanced(self, builder):
        from src.context.models import ContextPacket
        packet = ContextPacket(user_message="hello")
        prompt = builder.build_prompt(packet, style="unknown_value")
        assert "CONVERSATIONAL STYLE" not in prompt

    def test_default_is_balanced(self, builder):
        from src.context.models import ContextPacket
        packet = ContextPacket(user_message="hello")
        prompt = builder.build_prompt(packet)
        assert "CONVERSATIONAL STYLE" not in prompt

    def test_style_appears_before_state(self, builder):
        from src.context.models import ContextPacket
        packet = ContextPacket(user_message="hello")
        prompt = builder.build_prompt(packet, style="casual")
        style_idx = prompt.index("CONVERSATIONAL STYLE")
        state_idx = prompt.index("CURRENT STATE")
        assert style_idx < state_idx


class TestPreferencesAPI:
    """Integration tests for preferences API endpoints."""

    @pytest.fixture
    def client(self, tmp_path):
        with patch("src.core.config.get_private_vault_path", return_value=tmp_path), \
             patch("src.core.preferences.get_private_vault_path", return_value=tmp_path), \
             patch("src.api.main.get_ember_api_key", return_value=None):
            from src.api.main import app
            from fastapi.testclient import TestClient
            yield TestClient(app)

    def test_get_empty_preferences(self, client):
        resp = client.get("/v1/preferences")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_patch_and_get_preferences(self, client):
        resp = client.patch("/v1/preferences", json={"conversational_style": "casual"})
        assert resp.status_code == 200
        assert resp.json()["conversational_style"] == "casual"

        resp = client.get("/v1/preferences")
        assert resp.json()["conversational_style"] == "casual"

    def test_patch_multiple_keys(self, client):
        client.patch("/v1/preferences", json={"a": 1, "b": "two"})
        resp = client.get("/v1/preferences")
        data = resp.json()
        assert data["a"] == 1
        assert data["b"] == "two"

    def test_patch_overwrites_key(self, client):
        client.patch("/v1/preferences", json={"conversational_style": "casual"})
        client.patch("/v1/preferences", json={"conversational_style": "thoughtful"})
        resp = client.get("/v1/preferences")
        assert resp.json()["conversational_style"] == "thoughtful"
