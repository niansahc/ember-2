"""
tests/test_disk_encryption.py

Tests for disk encryption status detection (BitLocker, FileVault, LUKS).
All subprocess calls are mocked so tests run on any platform.
"""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from src.security.disk_encryption import (
    detect,
    _detect_bitlocker,
    _detect_filevault,
    _detect_luks,
)


# ---------------------------------------------------------------------------
# BitLocker (Windows)
# ---------------------------------------------------------------------------

class TestBitLocker:
    """Windows BitLocker detection via manage-bde."""

    def test_bitlocker_protection_on(self):
        mock_result = MagicMock()
        mock_result.stdout = (
            "Volume C: [OsDisk]\n"
            "    Conversion Status:    Fully Encrypted\n"
            "    Protection Status:    Protection On\n"
        )
        with patch("src.security.disk_encryption.subprocess.run", return_value=mock_result):
            result = _detect_bitlocker()
        assert result["enabled"] is True
        assert result["platform"] == "Windows"
        assert "BitLocker is active" in result["recommendation"]

    def test_bitlocker_protection_off(self):
        mock_result = MagicMock()
        mock_result.stdout = (
            "Volume C: [OsDisk]\n"
            "    Protection Status:    Protection Off\n"
        )
        with patch("src.security.disk_encryption.subprocess.run", return_value=mock_result):
            result = _detect_bitlocker()
        assert result["enabled"] is False
        assert "not active" in result["recommendation"]

    def test_bitlocker_unrecognized_output(self):
        mock_result = MagicMock()
        mock_result.stdout = "Something unexpected from manage-bde."
        with patch("src.security.disk_encryption.subprocess.run", return_value=mock_result):
            result = _detect_bitlocker()
        assert result["enabled"] is False
        assert "Could not determine" in result["recommendation"]

    def test_bitlocker_not_available(self):
        with patch(
            "src.security.disk_encryption.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = _detect_bitlocker()
        assert result["enabled"] is False
        assert "not available" in result["recommendation"]

    def test_bitlocker_subprocess_exception(self):
        with patch(
            "src.security.disk_encryption.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="manage-bde", timeout=10),
        ):
            result = _detect_bitlocker()
        assert result["enabled"] is False
        assert "Could not check" in result["recommendation"]


# ---------------------------------------------------------------------------
# FileVault (macOS)
# ---------------------------------------------------------------------------

class TestFileVault:
    """macOS FileVault detection via fdesetup."""

    def test_filevault_on(self):
        mock_result = MagicMock()
        mock_result.stdout = "FileVault is On."
        with patch("src.security.disk_encryption.subprocess.run", return_value=mock_result):
            result = _detect_filevault()
        assert result["enabled"] is True
        assert result["platform"] == "macOS"
        assert "FileVault is active" in result["recommendation"]

    def test_filevault_off(self):
        mock_result = MagicMock()
        mock_result.stdout = "FileVault is Off."
        with patch("src.security.disk_encryption.subprocess.run", return_value=mock_result):
            result = _detect_filevault()
        assert result["enabled"] is False
        assert "not active" in result["recommendation"]

    def test_filevault_unrecognized(self):
        mock_result = MagicMock()
        mock_result.stdout = "Unexpected output from fdesetup."
        with patch("src.security.disk_encryption.subprocess.run", return_value=mock_result):
            result = _detect_filevault()
        assert result["enabled"] is False

    def test_filevault_command_failure(self):
        with patch(
            "src.security.disk_encryption.subprocess.run",
            side_effect=OSError("fdesetup not found"),
        ):
            result = _detect_filevault()
        assert result["enabled"] is False
        assert "Could not check" in result["recommendation"]


# ---------------------------------------------------------------------------
# LUKS (Linux)
# ---------------------------------------------------------------------------

class TestLUKS:
    """Linux LUKS detection via lsblk."""

    def test_luks_active(self):
        mock_result = MagicMock()
        mock_result.stdout = "disk\npart\ncrypt\npart\n"
        with patch("src.security.disk_encryption.subprocess.run", return_value=mock_result):
            result = _detect_luks()
        assert result["enabled"] is True
        assert result["platform"] == "Linux"
        assert "LUKS" in result["recommendation"]

    def test_luks_not_active(self):
        mock_result = MagicMock()
        mock_result.stdout = "disk\npart\npart\n"
        with patch("src.security.disk_encryption.subprocess.run", return_value=mock_result):
            result = _detect_luks()
        assert result["enabled"] is False
        assert "No LUKS" in result["recommendation"]

    def test_luks_command_failure(self):
        with patch(
            "src.security.disk_encryption.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            result = _detect_luks()
        assert result["enabled"] is False
        assert "Could not check" in result["recommendation"]


# ---------------------------------------------------------------------------
# Platform dispatch (detect())
# ---------------------------------------------------------------------------

class TestDetectDispatch:
    """The top-level detect() function dispatches to the correct platform
    checker and returns a well-formed dict in all cases."""

    def test_dispatch_windows(self):
        mock_result = MagicMock()
        mock_result.stdout = "Protection Status:    Protection On"
        with patch("src.security.disk_encryption.platform.system", return_value="Windows"), \
             patch("src.security.disk_encryption.subprocess.run", return_value=mock_result):
            result = detect()
        assert result["platform"] == "Windows"
        assert result["enabled"] is True

    def test_dispatch_darwin(self):
        mock_result = MagicMock()
        mock_result.stdout = "FileVault is On."
        with patch("src.security.disk_encryption.platform.system", return_value="Darwin"), \
             patch("src.security.disk_encryption.subprocess.run", return_value=mock_result):
            result = detect()
        assert result["platform"] == "macOS"
        assert result["enabled"] is True

    def test_dispatch_linux(self):
        mock_result = MagicMock()
        mock_result.stdout = "disk\ncrypt\n"
        with patch("src.security.disk_encryption.platform.system", return_value="Linux"), \
             patch("src.security.disk_encryption.subprocess.run", return_value=mock_result):
            result = detect()
        assert result["platform"] == "Linux"
        assert result["enabled"] is True

    def test_dispatch_unknown_platform(self):
        with patch("src.security.disk_encryption.platform.system", return_value="FreeBSD"):
            result = detect()
        assert result["enabled"] is False
        assert result["platform"] == "FreeBSD"
        assert "Unsupported" in result["recommendation"]

    def test_result_always_has_three_keys(self):
        """Every code path must return exactly {enabled, platform, recommendation}."""
        mock_result = MagicMock()
        mock_result.stdout = ""
        platforms = ["Windows", "Darwin", "Linux", "FreeBSD"]
        for p in platforms:
            with patch("src.security.disk_encryption.platform.system", return_value=p), \
                 patch("src.security.disk_encryption.subprocess.run", return_value=mock_result):
                result = detect()
            assert set(result.keys()) == {"enabled", "platform", "recommendation"}, \
                f"Bad keys for platform {p}: {set(result.keys())}"
            assert isinstance(result["enabled"], bool)
            assert isinstance(result["platform"], str)
            assert isinstance(result["recommendation"], str)


# ---------------------------------------------------------------------------
# Endpoint integration
# ---------------------------------------------------------------------------

class TestDiskEncryptionEndpoint:
    """GET /v1/system/disk-encryption returns the detect() result."""

    def test_endpoint_returns_200_with_status(self):
        from unittest.mock import patch as mock_patch
        fake_result = {
            "enabled": True,
            "platform": "Windows",
            "recommendation": "BitLocker is active.",
        }
        with mock_patch("src.security.disk_encryption.detect", return_value=fake_result), \
             mock_patch("src.api.main.get_ember_api_key", return_value=None):
            from fastapi.testclient import TestClient
            from src.api.main import app
            resp = TestClient(app).get("/v1/system/disk-encryption")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True
        assert data["platform"] == "Windows"
        assert "BitLocker" in data["recommendation"]
