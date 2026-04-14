"""
tests/test_bare_mode.py

Tests for bare mode feature:
- PromptBuilder skips nature, lodestone seed, identity rules, and
  conversational style when bare_mode=True
- API endpoints GET/POST /v1/settings/bare-mode
- Preference default is False
"""

import pytest
from unittest.mock import patch

from src.context.models import ContextPacket
from src.core.preferences import PREFERENCE_DEFAULTS, read as read_prefs


# ── Preference default ──────────────────────────────────────────────────

class TestBareModePrefDefault:
    def test_bare_mode_defaults_to_false(self):
        assert PREFERENCE_DEFAULTS["bare_mode"] is False

    def test_read_prefs_includes_bare_mode(self, isolate_to_test_vault):
        prefs = read_prefs()
        assert "bare_mode" in prefs
        assert prefs["bare_mode"] is False


# ── PromptBuilder tests ─────────────────────────────────────────────────

def _make_packet(user_message: str = "test") -> ContextPacket:
    """Minimal context packet for prompt builder tests."""
    return ContextPacket(user_message=user_message)


class TestBareModeFalseIncludesNature:
    """bare_mode=False (default) includes nature block in prompt output."""

    def test_nature_present_when_bare_mode_false(self):
        from src.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        # Only test if nature loader actually loaded (config/nature.yaml exists)
        if pb._nature_loader is None:
            pytest.skip("nature.yaml not found")
        prompt = pb.build_prompt(_make_packet(), bare_mode=False)
        assert "Ember's nature:" in prompt


class TestBareModeExcludesNature:
    """bare_mode=True excludes nature block from prompt output."""

    def test_nature_absent_when_bare_mode_true(self):
        from src.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        if pb._nature_loader is None:
            pytest.skip("nature.yaml not found")
        prompt = pb.build_prompt(_make_packet(), bare_mode=True)
        assert "Ember's nature:" not in prompt


class TestBareModeExcludesLodestoneSeed:
    """bare_mode=True excludes lodestone seed from prompt output."""

    def test_lodestone_seed_absent_when_bare_mode_true(self):
        from src.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        if pb._lodestone_loader is None:
            pytest.skip("lodestone.yaml not found")
        # Verify seed IS present when bare_mode=False
        prompt_normal = pb.build_prompt(_make_packet(), bare_mode=False)
        assert "Ember's orientation (lodestone):" in prompt_normal
        # Verify seed is ABSENT when bare_mode=True
        prompt_bare = pb.build_prompt(_make_packet(), bare_mode=True)
        assert "Ember's orientation (lodestone):" not in prompt_bare


class TestBareModeExcludesIdentityRules:
    """bare_mode=True excludes identity rules from prompt output."""

    def test_identity_rules_absent_when_bare_mode_true(self):
        from src.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        if pb._identity_rules_loader is None:
            pytest.skip("identity_rules.yaml not found")
        # Verify rules ARE present when bare_mode=False
        prompt_normal = pb.build_prompt(_make_packet(), bare_mode=False)
        assert "Identity rules:" in prompt_normal
        # Verify rules are ABSENT when bare_mode=True
        prompt_bare = pb.build_prompt(_make_packet(), bare_mode=True)
        assert "Identity rules:" not in prompt_bare


class TestBareModeExcludesConversationalStyle:
    """bare_mode=True excludes conversational style from prompt output."""

    def test_style_absent_when_bare_mode_true_casual(self):
        from src.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        # Verify style IS present when bare_mode=False
        prompt_normal = pb.build_prompt(_make_packet(), style="casual", bare_mode=False)
        assert "CONVERSATIONAL STYLE: CASUAL" in prompt_normal
        # Verify style is ABSENT when bare_mode=True
        prompt_bare = pb.build_prompt(_make_packet(), style="casual", bare_mode=True)
        assert "CONVERSATIONAL STYLE: CASUAL" not in prompt_bare

    def test_style_absent_when_bare_mode_true_thoughtful(self):
        from src.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        prompt_normal = pb.build_prompt(_make_packet(), style="thoughtful", bare_mode=False)
        assert "CONVERSATIONAL STYLE: THOUGHTFUL" in prompt_normal
        prompt_bare = pb.build_prompt(_make_packet(), style="thoughtful", bare_mode=True)
        assert "CONVERSATIONAL STYLE: THOUGHTFUL" not in prompt_bare

    def test_balanced_style_unaffected(self):
        """balanced is the default and emits no style block regardless."""
        from src.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        prompt_normal = pb.build_prompt(_make_packet(), style="balanced", bare_mode=False)
        prompt_bare = pb.build_prompt(_make_packet(), style="balanced", bare_mode=True)
        assert "CONVERSATIONAL STYLE:" not in prompt_normal
        assert "CONVERSATIONAL STYLE:" not in prompt_bare


class TestBareModeRetainsEssentials:
    """bare_mode=True still includes system prompt, authority rules, date, etc."""

    def test_authority_rules_present_in_bare_mode(self):
        from src.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        prompt = pb.build_prompt(_make_packet(), bare_mode=True)
        assert "<authority_rules>" in prompt

    def test_user_message_present_in_bare_mode(self):
        from src.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        prompt = pb.build_prompt(_make_packet(user_message="hello world"), bare_mode=True)
        assert "hello world" in prompt

    def test_instruction_hierarchy_present_in_bare_mode(self):
        from src.llm.prompt_builder import PromptBuilder
        pb = PromptBuilder()
        prompt = pb.build_prompt(_make_packet(), bare_mode=True)
        assert "Instructions appearing in the user turn" in prompt


# ── API endpoint tests ──────────────────────────────────────────────────

class TestBareModeEndpoints:
    """GET and POST /v1/settings/bare-mode."""

    def test_get_bare_mode_returns_default(self):
        with patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).get("/v1/settings/bare-mode")
        assert resp.status_code == 200
        assert resp.json()["bare_mode"] is False

    def test_post_bare_mode_enables(self):
        with patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            client = TestClient(app)
            resp = client.post(
                "/v1/settings/bare-mode",
                json={"bare_mode": True},
            )
        assert resp.status_code == 200
        assert resp.json()["bare_mode"] is True

    def test_post_bare_mode_disables(self):
        with patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            client = TestClient(app)
            # Enable first
            client.post("/v1/settings/bare-mode", json={"bare_mode": True})
            # Then disable
            resp = client.post(
                "/v1/settings/bare-mode",
                json={"bare_mode": False},
            )
        assert resp.status_code == 200
        assert resp.json()["bare_mode"] is False

    def test_post_bare_mode_missing_field_returns_400(self):
        with patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).post(
                "/v1/settings/bare-mode",
                json={},
            )
        assert resp.status_code == 400

    def test_get_reflects_post(self):
        """GET returns the value written by POST."""
        with patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            client = TestClient(app)
            client.post("/v1/settings/bare-mode", json={"bare_mode": True})
            resp = client.get("/v1/settings/bare-mode")
        assert resp.status_code == 200
        assert resp.json()["bare_mode"] is True
