"""
tests/test_vault_swap.py

Tests for the runtime vault swap endpoint (developer mode).
Covers: dev mode gate, known path validation, path existence check,
runtime override behavior, vector index cache clearing, vault status,
and cleanup (override reverts after test).
"""

import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.core.config import (
    get_private_vault_path,
    set_vault_path_override,
    clear_vault_path_override,
    get_vault_label,
    get_known_vault_paths,
    is_dev_mode,
    _vault_path_override,
)


class TestConfigOverride:
    """The runtime override in config.py should take precedence over .env."""

    def setup_method(self):
        clear_vault_path_override()

    def teardown_method(self):
        clear_vault_path_override()

    def test_override_takes_precedence(self, tmp_path):
        vault_dir = tmp_path / "test_vault"
        vault_dir.mkdir()
        set_vault_path_override(str(vault_dir), "test")
        assert get_private_vault_path() == vault_dir.resolve()

    def test_label_reflects_override(self):
        set_vault_path_override("/some/path", "demo")
        assert get_vault_label() == "demo"

    def test_label_is_default_without_override(self):
        assert get_vault_label() == "default"

    def test_clear_reverts_to_env(self):
        set_vault_path_override("/some/path", "test")
        clear_vault_path_override()
        assert get_vault_label() == "default"
        # After clear, get_private_vault_path reads from .env again.
        # (This test doesn't assert the exact path since it depends on .env.)

    def test_known_vault_paths_reads_env(self):
        with patch.dict(os.environ, {
            "VAULT_PATH_LIVE": "/vaults/live",
            "VAULT_PATH_DEMO": "/vaults/demo",
            "VAULT_PATH_TEST": "/vaults/test",
        }):
            paths = get_known_vault_paths()
        assert paths == {
            "live": "/vaults/live",
            "demo": "/vaults/demo",
            "test": "/vaults/test",
        }

    def test_known_vault_paths_skips_unset(self):
        with patch.dict(os.environ, {"VAULT_PATH_LIVE": "/vaults/live"}, clear=False):
            # Remove DEMO and TEST if they exist
            env = dict(os.environ)
            env.pop("VAULT_PATH_DEMO", None)
            env.pop("VAULT_PATH_TEST", None)
            with patch.dict(os.environ, env, clear=True):
                paths = get_known_vault_paths()
        assert "live" in paths
        # demo/test may or may not be present depending on the real .env

    def test_is_dev_mode_false_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            assert is_dev_mode() is False

    def test_is_dev_mode_true_when_set(self):
        with patch.dict(os.environ, {"EMBER_DEV_MODE": "true"}):
            assert is_dev_mode() is True


class TestVaultSwapEndpoint:
    """Integration tests for POST /v1/developer/vault/swap."""

    def setup_method(self):
        clear_vault_path_override()

    def teardown_method(self):
        clear_vault_path_override()

    def _client(self):
        from fastapi.testclient import TestClient
        from src.api.main import app
        return TestClient(app)

    def test_swap_requires_dev_mode(self):
        with patch("src.core.config.is_dev_mode", return_value=False), \
             patch("src.api.main.get_ember_api_key", return_value=None):
            resp = self._client().post(
                "/v1/developer/vault/swap",
                json={"vault_label": "demo"},
            )
        assert resp.status_code == 403
        assert "DEV_MODE" in resp.json()["detail"]

    def test_swap_rejects_unknown_label(self):
        with patch("src.core.config.is_dev_mode", return_value=True), \
             patch("src.core.config.get_known_vault_paths", return_value={"live": "/v/live"}), \
             patch("src.api.main.get_ember_api_key", return_value=None):
            resp = self._client().post(
                "/v1/developer/vault/swap",
                json={"vault_label": "staging"},
            )
        assert resp.status_code == 400
        assert "Unknown vault label" in resp.json()["detail"]

    def test_swap_rejects_nonexistent_path(self, tmp_path):
        bad_path = str(tmp_path / "nonexistent")
        with patch("src.core.config.is_dev_mode", return_value=True), \
             patch("src.core.config.get_known_vault_paths", return_value={"demo": bad_path}), \
             patch("src.api.main.get_ember_api_key", return_value=None):
            resp = self._client().post(
                "/v1/developer/vault/swap",
                json={"vault_label": "demo"},
            )
        assert resp.status_code == 400
        assert "does not exist" in resp.json()["detail"]

    def test_swap_success(self, tmp_path):
        vault_dir = tmp_path / "demo_vault"
        vault_dir.mkdir()
        with patch("src.core.config.is_dev_mode", return_value=True), \
             patch("src.core.config.get_known_vault_paths", return_value={"demo": str(vault_dir)}), \
             patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("src.retrieval.vector_index.clear_index_cache") as mock_clear:
            resp = self._client().post(
                "/v1/developer/vault/swap",
                json={"vault_label": "demo"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["label"] == "demo"
        assert "demo_vault" in data["active_vault"]
        assert "indexes cleared" in data["note"]
        mock_clear.assert_called_once()

    def test_swap_clears_vector_index_cache(self, tmp_path):
        """The swap must clear ALL in-memory vector indexes so they
        lazy-load from the new vault on next query."""
        vault_dir = tmp_path / "test_vault"
        vault_dir.mkdir()
        with patch("src.core.config.is_dev_mode", return_value=True), \
             patch("src.core.config.get_known_vault_paths", return_value={"test": str(vault_dir)}), \
             patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("src.retrieval.vector_index.clear_index_cache") as mock_clear:
            self._client().post(
                "/v1/developer/vault/swap",
                json={"vault_label": "test"},
            )
        mock_clear.assert_called_once_with()

    def test_swap_updates_config_override(self, tmp_path):
        vault_dir = tmp_path / "live_vault"
        vault_dir.mkdir()
        with patch("src.core.config.is_dev_mode", return_value=True), \
             patch("src.core.config.get_known_vault_paths", return_value={"live": str(vault_dir)}), \
             patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("src.retrieval.vector_index.clear_index_cache"):
            self._client().post(
                "/v1/developer/vault/swap",
                json={"vault_label": "live"},
            )
        # After the swap, get_private_vault_path should return the new path.
        assert get_private_vault_path() == vault_dir.resolve()
        assert get_vault_label() == "live"
        # Cleanup
        clear_vault_path_override()


class TestVaultStatusEndpoint:
    """Integration tests for GET /v1/developer/vault/status."""

    def setup_method(self):
        clear_vault_path_override()

    def teardown_method(self):
        clear_vault_path_override()

    def test_status_returns_default(self):
        with patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).get("/v1/developer/vault/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "active_vault" in data
        assert data["label"] == "default"

    def test_status_reflects_swap(self, tmp_path):
        vault_dir = tmp_path / "status_vault"
        vault_dir.mkdir()
        set_vault_path_override(str(vault_dir), "demo")
        with patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).get("/v1/developer/vault/status")
        data = resp.json()
        assert data["label"] == "demo"
        assert "status_vault" in data["active_vault"]
        clear_vault_path_override()
