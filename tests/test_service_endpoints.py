"""
tests/test_service_endpoints.py

Tests for service health, restart, and developer status endpoints.
"""

from unittest.mock import patch, MagicMock

import pytest


class TestHealthCheckDocker:
    """GET /api/health now includes a docker field."""

    def _client(self):
        with patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            return TestClient(app)

    def test_health_includes_docker_field(self):
        with patch("src.api.main._check_docker_status", return_value="ok"), \
             patch("src.api.main.get_ember_api_key", return_value=None):
            resp = self._client().get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "docker" in data
        assert data["docker"] in ("ok", "down")

    def test_health_docker_ok_when_searxng_responds(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch("src.api.main.get_ember_api_key", return_value=None):
            with patch("httpx.get", return_value=mock_resp):
                from src.api.main import _check_docker_status
                assert _check_docker_status() == "ok"

    def test_health_docker_down_when_searxng_unreachable(self):
        with patch("src.api.main.get_ember_api_key", return_value=None):
            with patch("httpx.get", side_effect=ConnectionError):
                from src.api.main import _check_docker_status
                assert _check_docker_status() == "down"

    def test_health_docker_down_on_500(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        with patch("src.api.main.get_ember_api_key", return_value=None):
            with patch("httpx.get", return_value=mock_resp):
                from src.api.main import _check_docker_status
                assert _check_docker_status() == "down"


class TestServiceRestart:
    """POST /v1/service/{name}/restart."""

    def _client(self):
        with patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            return TestClient(app)

    def test_restart_api_writes_signal_and_returns_200(self):
        """API restart now writes a signal file for the watchdog instead
        of returning 501. The watchdog picks up the signal and restarts."""
        with patch("src.api.main.get_ember_api_key", return_value=None):
            resp = self._client().post("/v1/service/api/restart")
        assert resp.status_code == 200
        assert resp.json()["service"] == "api"
        assert "watchdog" in resp.json()["note"]
        # Clean up signal file written during test
        from pathlib import Path
        sig = Path(__file__).resolve().parents[1] / "ember_restart.signal"
        if sig.exists():
            sig.unlink()

    def test_restart_docker_succeeds(self):
        with patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("subprocess.Popen") as mock_popen:
            resp = self._client().post("/v1/service/docker/restart")
        assert resp.status_code == 200
        assert resp.json()["status"] == "restarting"
        mock_popen.assert_called_once()
        # Verify the command is docker compose restart
        call_args = mock_popen.call_args
        assert call_args[0][0] == ["docker", "compose", "restart"]

    def test_restart_docker_missing_command(self):
        with patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("subprocess.Popen", side_effect=FileNotFoundError):
            resp = self._client().post("/v1/service/docker/restart")
        assert resp.status_code == 500
        assert "not found" in resp.json()["detail"]

    def test_restart_unknown_service(self):
        with patch("src.api.main.get_ember_api_key", return_value=None):
            resp = self._client().post("/v1/service/redis/restart")
        assert resp.status_code == 400
        assert "Unknown service" in resp.json()["detail"]


class TestDeveloperStatus:
    """GET /v1/developer/status."""

    def test_returns_dev_mode_and_vault_info(self):
        with patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("src.core.config.is_dev_mode", return_value=True), \
             patch("src.core.config.get_known_vault_paths", return_value={
                 "live": "/vaults/live", "demo": "/vaults/demo",
             }):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).get("/v1/developer/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["dev_mode"] is True
        assert "active_vault" in data
        assert "label" in data["active_vault"]
        assert "path" in data["active_vault"]
        assert isinstance(data["available_vaults"], list)
        assert len(data["available_vaults"]) == 2

    def test_accessible_without_dev_mode(self):
        """Status endpoint must be readable even when dev mode is off."""
        with patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("src.core.config.is_dev_mode", return_value=False):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).get("/v1/developer/status")
        assert resp.status_code == 200
        assert resp.json()["dev_mode"] is False


class TestDeveloperVaults:
    """GET /v1/developer/vaults."""

    def test_returns_list_of_configured_vaults(self):
        with patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("src.core.config.get_known_vault_paths", return_value={
                 "live": "/vaults/live",
                 "test": "/vaults/test",
             }):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).get("/v1/developer/vaults")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        labels = {v["label"] for v in data}
        assert labels == {"live", "test"}

    def test_returns_empty_when_none_configured(self):
        with patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("src.core.config.get_known_vault_paths", return_value={}):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).get("/v1/developer/vaults")
        assert resp.status_code == 200
        assert resp.json() == []
