"""
src/core/preferences.py

User preferences store. Simple mutable JSON file in the vault root.

Unlike memory records, preferences are mutable by design — they represent
the user's current choices, not historical artifacts. The file is created
on first write if it does not exist.

Location: private_vault/preferences.json
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.core.config import get_private_vault_path

logger = logging.getLogger("ember.preferences")

# Default values for all known preference fields. GET /v1/preferences
# merges these under the stored values so the response always includes
# every known field, even if the user has never set it. New preference
# fields should be added here with their default value.
PREFERENCE_DEFAULTS: dict = {
    "conversational_style": "balanced",
    "web_search_autonomous": True,
    "first_run_tour_complete": False,
    "context_length": 8192,
    "bare_mode": False,
}


def _get_prefs_path(vault_path: Path | None = None) -> Path:
    """Return the path to the preferences file."""
    vault = vault_path or get_private_vault_path()
    return vault / "preferences.json"


def read(vault_path: Path | None = None) -> dict:
    """
    Read all preferences. Returns PREFERENCE_DEFAULTS merged with stored
    values — stored values take priority. The response always includes
    every known field so callers don't need to handle missing keys.
    """
    path = _get_prefs_path(vault_path)
    stored: dict = {}
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[PREFERENCES] Failed to read %s: %s", path, exc)

    return {**PREFERENCE_DEFAULTS, **stored}


def get(key: str, default=None, vault_path: Path | None = None):
    """Get a single preference value, with a default if not set."""
    return read(vault_path).get(key, default)


def write(key: str, value, vault_path: Path | None = None) -> None:
    """Set a single preference value. Creates the file if needed."""
    path = _get_prefs_path(vault_path)
    prefs = read(vault_path)
    prefs[key] = value
    path.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")


def update(updates: dict, vault_path: Path | None = None) -> None:
    """Update multiple preference values at once."""
    path = _get_prefs_path(vault_path)
    prefs = read(vault_path)
    prefs.update(updates)
    path.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")
