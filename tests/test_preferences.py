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

    def test_read_empty_vault_returns_defaults(self, tmp_path):
        result = preferences.read(vault_path=tmp_path)
        # Empty vault returns PREFERENCE_DEFAULTS — all known fields with defaults.
        assert result == preferences.PREFERENCE_DEFAULTS

    def test_write_and_read(self, tmp_path):
        preferences.write("conversational_style", "casual", vault_path=tmp_path)
        result = preferences.read(vault_path=tmp_path)
        assert result["conversational_style"] == "casual"
        # Defaults still present for fields not explicitly written
        assert result["web_search_autonomous"] is True

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
        assert result["a"] == 1
        assert result["b"] == 2

    def test_handles_corrupted_file(self, tmp_path):
        prefs_path = tmp_path / "preferences.json"
        prefs_path.write_text("not json!", encoding="utf-8")
        result = preferences.read(vault_path=tmp_path)
        # Corrupted file falls back to defaults
        assert result == preferences.PREFERENCE_DEFAULTS

    def test_overwrites_existing_key(self, tmp_path):
        preferences.write("style", "casual", vault_path=tmp_path)
        preferences.write("style", "thoughtful", vault_path=tmp_path)
        assert preferences.get("style", vault_path=tmp_path) == "thoughtful"


class TestWebSearchAutonomousMigration:
    """B-WEB-001: v0.15.x → v0.16.0 sentinel-gated one-shot migration.

    Old vaults with web_search_autonomous=False AND no prefs_schema_version
    sentinel get upgraded to True on first read. A deliberate False set
    after the v0.17.0 ask-first UI ships (which carries the sentinel) is
    preserved.
    """

    def test_upgrade_v0_15_false_to_v0_16_true_one_shot(self, tmp_path):
        """Stale False with no sentinel: upgrade to True and write sentinel."""
        prefs_path = tmp_path / "preferences.json"
        prefs_path.write_text(
            json.dumps({"web_search_autonomous": False}), encoding="utf-8"
        )

        result = preferences.read(vault_path=tmp_path)
        assert result["web_search_autonomous"] is True

        # File on disk should now contain the sentinel
        on_disk = json.loads(prefs_path.read_text(encoding="utf-8"))
        assert on_disk["web_search_autonomous"] is True
        assert on_disk["prefs_schema_version"] == 1

    def test_second_read_after_migration_is_noop(self, tmp_path):
        """Second read sees the sentinel and does not re-migrate or re-write."""
        prefs_path = tmp_path / "preferences.json"
        prefs_path.write_text(
            json.dumps({"web_search_autonomous": False}), encoding="utf-8"
        )

        preferences.read(vault_path=tmp_path)  # triggers migration
        first_mtime = prefs_path.stat().st_mtime_ns

        # Second call: nothing should change on disk
        result = preferences.read(vault_path=tmp_path)
        assert result["web_search_autonomous"] is True
        assert prefs_path.stat().st_mtime_ns == first_mtime

    def test_deliberate_false_with_sentinel_preserved(self, tmp_path):
        """Once the user-facing toggle exists, a deliberate False (with sentinel)
        must NOT be flipped back to True."""
        prefs_path = tmp_path / "preferences.json"
        prefs_path.write_text(
            json.dumps({
                "web_search_autonomous": False,
                "prefs_schema_version": 1,
            }),
            encoding="utf-8",
        )

        result = preferences.read(vault_path=tmp_path)
        assert result["web_search_autonomous"] is False

        # And no rewrite happened — file unchanged
        on_disk = json.loads(prefs_path.read_text(encoding="utf-8"))
        assert on_disk["web_search_autonomous"] is False
        assert on_disk["prefs_schema_version"] == 1

    def test_true_with_no_sentinel_does_not_trigger_migration(self, tmp_path):
        """If the stored value is already True, no migration needed even
        without the sentinel — gate is False-specific."""
        prefs_path = tmp_path / "preferences.json"
        prefs_path.write_text(
            json.dumps({"web_search_autonomous": True}), encoding="utf-8"
        )

        result = preferences.read(vault_path=tmp_path)
        assert result["web_search_autonomous"] is True

        # File unchanged — no sentinel added (we don't migrate True files)
        on_disk = json.loads(prefs_path.read_text(encoding="utf-8"))
        assert "prefs_schema_version" not in on_disk

    def test_empty_vault_does_not_trigger_migration(self, tmp_path):
        """No file → no migration — defaults already give True."""
        result = preferences.read(vault_path=tmp_path)
        assert result["web_search_autonomous"] is True
        # File should not have been created by the read
        assert not (tmp_path / "preferences.json").exists()


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
        state_idx = prompt.index("<current_state>")
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

    def test_get_empty_preferences_returns_defaults(self, client):
        resp = client.get("/v1/preferences")
        assert resp.status_code == 200
        data = resp.json()
        # Empty vault returns defaults for all known fields
        assert data["web_search_autonomous"] is True
        assert data["conversational_style"] == "balanced"
        assert data["first_run_tour_complete"] is False

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
