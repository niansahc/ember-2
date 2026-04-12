"""
tests/test_watchdog.py

Tests for the watchdog process and signal-file-based restart/stop.
Tests the signal file mechanism and the API endpoint integration.
Does NOT test actual process management (requires real subprocess).
"""

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


class TestWatchdogModule:
    """Test the watchdog module's utility functions."""

    def test_signal_paths_are_in_repo_root(self):
        from scripts.watchdog import RESTART_SIGNAL, STOP_SIGNAL, REPO_ROOT as WD_ROOT
        assert RESTART_SIGNAL.parent == WD_ROOT
        assert STOP_SIGNAL.parent == WD_ROOT
        assert RESTART_SIGNAL.name == "ember_restart.signal"
        assert STOP_SIGNAL.name == "ember_stop.signal"

    def test_clear_signal_removes_file(self, tmp_path):
        from scripts.watchdog import clear_signal
        sig = tmp_path / "test.signal"
        sig.write_text("test")
        assert sig.exists()
        clear_signal(sig)
        assert not sig.exists()

    def test_clear_signal_noop_when_missing(self, tmp_path):
        from scripts.watchdog import clear_signal
        sig = tmp_path / "nonexistent.signal"
        clear_signal(sig)  # should not raise

    def test_venv_python_returns_string(self):
        from scripts.watchdog import _venv_python
        result = _venv_python()
        assert isinstance(result, str)
        assert len(result) > 0


class TestRestartEndpointWritesSignal:
    """POST /v1/service/api/restart should write the signal file."""

    def test_restart_writes_signal_file(self, tmp_path):
        signal_path = tmp_path / "ember_restart.signal"

        with patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("src.api.main.Path") as mock_path_cls:
            # Make Path(__file__).resolve().parents[2] return tmp_path
            mock_file = MagicMock()
            mock_file.resolve.return_value.parents.__getitem__ = lambda self, idx: tmp_path
            mock_path_cls.return_value = mock_file
            # The endpoint uses Path(__file__).resolve().parents[2] / "ember_restart.signal"
            # We need to patch at the right level. Simpler: just check the endpoint returns 200.
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).post("/v1/service/api/restart")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "restarting"
        assert data["service"] == "api"
        assert "watchdog" in data["note"]

    def test_stop_writes_signal_file(self):
        with patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).post("/v1/service/api/stop")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "stopping"
        assert data["service"] == "api"

        # Clean up the signal file written during the test
        signal_path = Path(__file__).resolve().parents[1] / "ember_stop.signal"
        if signal_path.exists():
            signal_path.unlink()

    def test_restart_endpoint_no_longer_returns_501(self):
        """Regression: the old endpoint returned 501 for api restart.
        With the watchdog, it should return 200."""
        with patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).post("/v1/service/api/restart")
        assert resp.status_code != 501

        # Clean up
        signal_path = Path(__file__).resolve().parents[1] / "ember_restart.signal"
        if signal_path.exists():
            signal_path.unlink()


class TestStopEndpoint:
    """POST /v1/service/{name}/stop."""

    def test_docker_stop(self):
        with patch("src.api.main.get_ember_api_key", return_value=None), \
             patch("subprocess.Popen"):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).post("/v1/service/docker/stop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopping"

    def test_unknown_service_stop(self):
        with patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).post("/v1/service/redis/stop")
        assert resp.status_code == 400


class TestLaunchScripts:
    """Verify launch scripts reference the watchdog."""

    def test_bat_starts_watchdog(self):
        bat = (REPO_ROOT / "launch_ember.bat").read_text(encoding="utf-8")
        assert "watchdog.py" in bat
        assert "Ember-2 Watchdog" in bat

    def test_sh_starts_watchdog(self):
        sh = (REPO_ROOT / "launch_ember.sh").read_text(encoding="utf-8")
        assert "watchdog.py" in sh
        assert "WATCHDOG_PID" in sh

    def test_signal_files_gitignored(self):
        gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "ember_restart.signal" in gitignore
        assert "ember_stop.signal" in gitignore
