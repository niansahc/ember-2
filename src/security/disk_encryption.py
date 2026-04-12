"""
src/security/disk_encryption.py

Detect whether full-disk encryption is enabled on the host OS.
Checks BitLocker (Windows), FileVault (macOS), or LUKS (Linux).

Returns a status dict:
    {"enabled": bool, "platform": str, "recommendation": str}

All subprocess calls use a short timeout and broad exception handling
so a missing tool, insufficient privileges, or non-standard OS never
crashes the endpoint. When detection fails, enabled=False with a
recommendation to verify manually.
"""

from __future__ import annotations

import platform
import subprocess


def detect() -> dict:
    """Detect disk encryption status for the current platform."""
    system = platform.system()

    if system == "Windows":
        return _detect_bitlocker()
    if system == "Darwin":
        return _detect_filevault()
    if system == "Linux":
        return _detect_luks()

    return {
        "enabled": False,
        "platform": system,
        "recommendation": (
            f"Unsupported platform ({system}). "
            "Please verify full-disk encryption status manually."
        ),
    }


def _detect_bitlocker() -> dict:
    """Check BitLocker on the system drive via manage-bde."""
    try:
        result = subprocess.run(
            ["manage-bde", "-status", "C:"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout.lower()
        # manage-bde outputs "Protection Status: Protection On" when active.
        if "protection on" in output:
            return {
                "enabled": True,
                "platform": "Windows",
                "recommendation": (
                    "BitLocker is active on the system drive. "
                    "Your vault data is encrypted at rest."
                ),
            }
        if "protection off" in output:
            return {
                "enabled": False,
                "platform": "Windows",
                "recommendation": (
                    "BitLocker is installed but not active on the system drive. "
                    "Enable BitLocker in Settings > Privacy & Security > "
                    "Device encryption to protect your vault data at rest."
                ),
            }
        # manage-bde ran but output is unrecognized — report conservatively.
        return {
            "enabled": False,
            "platform": "Windows",
            "recommendation": (
                "Could not determine BitLocker status. "
                "Check Settings > Privacy & Security > Device encryption."
            ),
        }
    except FileNotFoundError:
        return {
            "enabled": False,
            "platform": "Windows",
            "recommendation": (
                "BitLocker is not available on this edition of Windows. "
                "Consider upgrading to Windows Pro or using a third-party "
                "encryption tool to protect your vault data at rest."
            ),
        }
    except Exception:
        return {
            "enabled": False,
            "platform": "Windows",
            "recommendation": (
                "Could not check BitLocker status (insufficient privileges "
                "or tool not found). Run as administrator or check Settings "
                "> Privacy & Security > Device encryption."
            ),
        }


def _detect_filevault() -> dict:
    """Check FileVault on macOS via fdesetup."""
    try:
        result = subprocess.run(
            ["fdesetup", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = result.stdout.lower()
        if "filevault is on" in output:
            return {
                "enabled": True,
                "platform": "macOS",
                "recommendation": (
                    "FileVault is active. "
                    "Your vault data is encrypted at rest."
                ),
            }
        if "filevault is off" in output:
            return {
                "enabled": False,
                "platform": "macOS",
                "recommendation": (
                    "FileVault is not active. Enable it in System Settings "
                    "> Privacy & Security > FileVault to protect your vault "
                    "data at rest."
                ),
            }
        return {
            "enabled": False,
            "platform": "macOS",
            "recommendation": (
                "Could not determine FileVault status. "
                "Check System Settings > Privacy & Security > FileVault."
            ),
        }
    except Exception:
        return {
            "enabled": False,
            "platform": "macOS",
            "recommendation": (
                "Could not check FileVault status. "
                "Check System Settings > Privacy & Security > FileVault."
            ),
        }


def _detect_luks() -> dict:
    """Check LUKS on Linux by looking for active crypt devices."""
    try:
        result = subprocess.run(
            ["lsblk", "-o", "TYPE", "--noheadings"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if "crypt" in result.stdout.lower():
            return {
                "enabled": True,
                "platform": "Linux",
                "recommendation": (
                    "LUKS encryption detected (active crypt device). "
                    "Your vault data is encrypted at rest."
                ),
            }
        return {
            "enabled": False,
            "platform": "Linux",
            "recommendation": (
                "No LUKS-encrypted volumes detected. If your disk is "
                "encrypted via a different mechanism (e.g. dm-crypt "
                "without LUKS headers, ecryptfs, or hardware FDE), "
                "this check may not detect it. Verify with "
                "'lsblk -o NAME,TYPE' or your distribution's disk "
                "management tool."
            ),
        }
    except Exception:
        return {
            "enabled": False,
            "platform": "Linux",
            "recommendation": (
                "Could not check disk encryption status. "
                "Verify with 'lsblk -o NAME,TYPE' or your distribution's "
                "disk management tool."
            ),
        }
