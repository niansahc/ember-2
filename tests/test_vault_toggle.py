"""
tests/test_vault_toggle.py

Tests for the per-conversation vault toggle feature.

Covers:
- vault_enabled=True (default): context builds normally, vault writes happen
- vault_enabled=False: empty ContextPacket, no vault writes, no session, no tasks
- Settings endpoints for toggling the global vault_toggle_enabled preference
- Global vault_toggle_enabled=False overrides per-request vault_enabled=False
"""

import json
import uuid
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from src.context.models import ContextPacket


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chat_body(vault_enabled=True, stream=False):
    """Build a minimal chat completions request body."""
    return {
        "model": "test-model",
        "messages": [{"role": "user", "content": "Hello Ember"}],
        "stream": stream,
        "vault_enabled": vault_enabled,
    }


def _dummy_context_packet(msg="Hello Ember"):
    """Return a ContextPacket with one fake memory item for normal builds."""
    from src.context.models import ContextItem
    return ContextPacket(
        user_message=msg,
        memory_items=[ContextItem(
            id="mem-001", content="Synthetic memory", source="test",
            item_type="conversation", score=0.9,
        )],
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path):
    """FastAPI TestClient with all heavy dependencies mocked out.

    Mocks: LLM adapter (returns a canned reply), context_service,
    write_memory, _ensure_session, onboarding_service, preferences.
    Auth is disabled (get_ember_api_key returns None).
    """
    prefs_store: dict = {}

    def _read_prefs(vault_path=None):
        return {**prefs_store}

    def _get_pref(key, default=None, vault_path=None):
        return prefs_store.get(key, default)

    def _update_prefs(data, vault_path=None):
        prefs_store.update(data)

    # Build a mock LLM adapter that returns a simple reply
    mock_llm = MagicMock()
    mock_llm.generate_response.return_value = "Test reply from Ember"
    mock_llm.prompt_builder = MagicMock()
    mock_llm.prompt_builder.conversation_buffer = MagicMock()
    mock_llm.prompt_builder.conversation_buffer.get_recent.return_value = []
    mock_llm.prompt_builder.build_prompt.return_value = "system prompt"
    mock_llm.prompt_builder.build_vision_prompt.return_value = "vision prompt"

    mock_context = MagicMock()
    mock_context.build_context.return_value = _dummy_context_packet()

    mock_write = MagicMock()
    mock_ensure = MagicMock()
    mock_onboarding = MagicMock()
    mock_onboarding.is_active.return_value = False

    with patch("src.core.config.get_private_vault_path", return_value=tmp_path), \
         patch("src.core.preferences.get_private_vault_path", return_value=tmp_path), \
         patch("src.core.preferences.read", side_effect=_read_prefs), \
         patch("src.core.preferences.get", side_effect=_get_pref), \
         patch("src.core.preferences.update", side_effect=_update_prefs), \
         patch("src.api.main.get_ember_api_key", return_value=None), \
         patch("src.api.openai_adapter.context_service", mock_context), \
         patch("src.api.openai_adapter.llm_adapter", mock_llm), \
         patch("src.api.openai_adapter.write_memory", mock_write), \
         patch("src.api.openai_adapter._ensure_session", mock_ensure), \
         patch("src.api.openai_adapter.onboarding_service", mock_onboarding), \
         patch("src.api.openai_adapter.get_session", return_value=None), \
         patch("src.api.openai_adapter._is_override_attempt", return_value=False), \
         patch("src.llm.coaching_filter.filter_coaching_frame", side_effect=lambda r, *a, **k: r), \
         patch("src.api.openai_adapter._background_state_extraction", return_value=None), \
         patch("src.api.openai_adapter._detect_and_write_commitment", return_value=None), \
         patch("src.api.openai_adapter._detect_task_in_response", return_value=None), \
         patch("src.api.openai_adapter._write_pending_confirmation", return_value=None):

        from src.api.main import app
        from fastapi.testclient import TestClient
        _client = TestClient(app)

        # Expose mocks for assertions
        _client._mock_context = mock_context
        _client._mock_write = mock_write
        _client._mock_ensure = mock_ensure
        _client._mock_llm = mock_llm
        _client._prefs_store = prefs_store

        yield _client


# ---------------------------------------------------------------------------
# 1. Default vault_enabled=True — context builds normally
# ---------------------------------------------------------------------------


class TestVaultEnabledDefault:
    """When vault_enabled=True (default), context_service.build_context is called."""

    def test_default_builds_context(self, client):
        resp = client.post(
            "/v1/chat/completions",
            json=_chat_body(vault_enabled=True),
            headers={"X-Test-Session": "false"},
        )
        assert resp.status_code == 200
        client._mock_context.build_context.assert_called()

    def test_omitted_vault_enabled_defaults_true(self, client):
        body = _chat_body()
        del body["vault_enabled"]
        resp = client.post(
            "/v1/chat/completions",
            json=body,
            headers={"X-Test-Session": "false"},
        )
        assert resp.status_code == 200
        client._mock_context.build_context.assert_called()


# ---------------------------------------------------------------------------
# 2. vault_enabled=False — empty ContextPacket, no vault reads
# ---------------------------------------------------------------------------


class TestVaultDisabledNoReads:
    """When vault_enabled=False, build_context is NOT called."""

    def test_skip_vault_creates_empty_context(self, client):
        resp = client.post(
            "/v1/chat/completions",
            json=_chat_body(vault_enabled=False),
            headers={"X-Test-Session": "false"},
        )
        assert resp.status_code == 200
        # build_context should not be called — the adapter creates an empty
        # ContextPacket inline instead.
        client._mock_context.build_context.assert_not_called()


# ---------------------------------------------------------------------------
# 3. vault_enabled=False — no write_memory calls
# ---------------------------------------------------------------------------


class TestVaultDisabledNoWrites:
    """When vault_enabled=False, write_memory is never called."""

    def test_no_write_memory(self, client):
        resp = client.post(
            "/v1/chat/completions",
            json=_chat_body(vault_enabled=False),
            headers={"X-Test-Session": "false"},
        )
        assert resp.status_code == 200
        client._mock_write.assert_not_called()


# ---------------------------------------------------------------------------
# 4. vault_enabled=False — _ensure_session called with test=True
# ---------------------------------------------------------------------------


class TestVaultDisabledNoSession:
    """When vault_enabled=False, _ensure_session is called with test=True,
    which causes it to skip vault writes internally."""

    def test_ensure_session_skipped(self, client):
        resp = client.post(
            "/v1/chat/completions",
            json=_chat_body(vault_enabled=False),
            headers={"X-Test-Session": "false"},
        )
        assert resp.status_code == 200
        # _ensure_session is called but with test=True, so no session record
        # is written to the vault.
        client._mock_ensure.assert_called_once()
        _, kwargs = client._mock_ensure.call_args
        assert kwargs.get("test") is True


# ---------------------------------------------------------------------------
# 5. vault_enabled=False — task creation paths skipped
# ---------------------------------------------------------------------------


class TestVaultDisabledNoTasks:
    """When vault_enabled=False, background task detection threads are not
    started. We verify this indirectly: write_memory is not called (tasks
    would write to vault), and the mock task functions are never invoked."""

    def test_no_task_detection(self, client):
        with patch("src.api.openai_adapter.threading") as mock_threading:
            resp = client.post(
                "/v1/chat/completions",
                json=_chat_body(vault_enabled=False),
                headers={"X-Test-Session": "false"},
            )
            assert resp.status_code == 200
            # No background threads should be started for task/state/commitment
            # detection when vault is disabled.
            mock_threading.Thread.assert_not_called()


# ---------------------------------------------------------------------------
# 6. POST /v1/settings/vault-enabled toggles the preference
# ---------------------------------------------------------------------------


class TestVaultTogglePostEndpoint:
    """POST /v1/settings/vault-enabled updates vault_toggle_enabled pref."""

    def test_set_vault_toggle_false(self, client):
        resp = client.post(
            "/v1/settings/vault-enabled",
            json={"vault_toggle_enabled": False},
        )
        assert resp.status_code == 200
        assert resp.json()["vault_toggle_enabled"] is False

    def test_set_vault_toggle_true(self, client):
        # First disable, then re-enable
        client.post(
            "/v1/settings/vault-enabled",
            json={"vault_toggle_enabled": False},
        )
        resp = client.post(
            "/v1/settings/vault-enabled",
            json={"vault_toggle_enabled": True},
        )
        assert resp.status_code == 200
        assert resp.json()["vault_toggle_enabled"] is True

    def test_missing_field_returns_400(self, client):
        resp = client.post(
            "/v1/settings/vault-enabled",
            json={"unrelated_key": True},
        )
        assert resp.status_code == 400

    def test_boolean_coercion(self, client):
        # Truthy value should be coerced to True
        resp = client.post(
            "/v1/settings/vault-enabled",
            json={"vault_toggle_enabled": 1},
        )
        assert resp.status_code == 200
        assert resp.json()["vault_toggle_enabled"] is True


# ---------------------------------------------------------------------------
# 7. GET /v1/settings/vault-enabled returns current state
# ---------------------------------------------------------------------------


class TestVaultToggleGetEndpoint:
    """GET /v1/settings/vault-enabled returns the current toggle value."""

    def test_default_is_true(self, client):
        resp = client.get("/v1/settings/vault-enabled")
        assert resp.status_code == 200
        assert resp.json()["vault_toggle_enabled"] is True

    def test_reflects_post_change(self, client):
        client.post(
            "/v1/settings/vault-enabled",
            json={"vault_toggle_enabled": False},
        )
        resp = client.get("/v1/settings/vault-enabled")
        assert resp.status_code == 200
        assert resp.json()["vault_toggle_enabled"] is False


# ---------------------------------------------------------------------------
# 8. Global vault_toggle_enabled=False overrides per-request vault_enabled
# ---------------------------------------------------------------------------


class TestGlobalToggleOverride:
    """When vault_toggle_enabled=False globally, per-request vault_enabled=False
    is ignored — the vault is always on."""

    def test_global_disable_forces_vault_on(self, client):
        # Disable the global toggle feature
        client.post(
            "/v1/settings/vault-enabled",
            json={"vault_toggle_enabled": False},
        )
        # Now send a request with vault_enabled=False — should be ignored
        resp = client.post(
            "/v1/chat/completions",
            json=_chat_body(vault_enabled=False),
            headers={"X-Test-Session": "false"},
        )
        assert resp.status_code == 200
        # build_context SHOULD be called because the global toggle overrides
        # the per-request setting back to True.
        client._mock_context.build_context.assert_called()

    def test_global_disable_still_allows_vault_on(self, client):
        # Disable the global toggle feature
        client.post(
            "/v1/settings/vault-enabled",
            json={"vault_toggle_enabled": False},
        )
        # Request with vault_enabled=True — should work normally
        resp = client.post(
            "/v1/chat/completions",
            json=_chat_body(vault_enabled=True),
            headers={"X-Test-Session": "false"},
        )
        assert resp.status_code == 200
        client._mock_context.build_context.assert_called()
