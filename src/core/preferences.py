"""
src/core/preferences.py

User preferences store. Simple mutable JSON file in the vault root.

Unlike memory records, preferences are mutable by design — they represent
the user's current choices, not historical artifacts. The file is created
on first write if it does not exist.

Location: private_vault/preferences.json
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.core.config import get_private_vault_path
from src.core.jsonio import JsonIoError, safe_read_json, safe_write_json

logger = logging.getLogger("ember.preferences")

# S9: tracks vault paths whose B-WEB-001 migration write has permanently
# failed (e.g. read-only vault, full disk). After the first failure for a
# given path, subsequent reads skip the write attempt and downgrade the log
# to DEBUG to avoid log flooding. Cleared on successful migration write so
# a recovered vault re-enables the warning + retry path.
_migration_write_failed_paths: set[Path] = set()

# Default values for all known preference fields. GET /v1/preferences
# merges these under the stored values so the response always includes
# every known field, even if the user has never set it. New preference
# fields should be added here with their default value.
PREFERENCE_DEFAULTS: dict = {
    "conversational_style": "balanced",
    "web_search_autonomous": True,
    "first_run_tour_complete": False,
    # context_length defaults to None — the LLM adapter resolves it from
    # MODEL_CONTEXT_WINDOWS for the active model when no explicit override is
    # set (B-QUAL-001). The key is present (not absent) so the GET /v1/preferences
    # response contract — every known field always returned — is preserved.
    "context_length": None,
    "bare_mode": False,
    # Issue #138: gates whether an attached image is sent to the vision
    # preprocessor at all. Defaults True so existing installs keep the
    # ADR-032 auto-trigger behavior unless a user explicitly opts out.
    "vision_enabled": True,
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

    B-WEB-001 / v0.16.0 migration: v0.15.x vaults stored
    web_search_autonomous=False as the active default. v0.16.0 flipped the
    default to True but did not migrate stored values, so users with old
    vaults silently kept the False — first weather query triggered ask-first
    instead of autonomous search. This function performs a one-shot, atomic
    in-read migration: if the stored file has web_search_autonomous=False AND
    no prefs_schema_version sentinel, the value is upgraded to True and a
    schema version is written before this function returns. Files written by
    v0.17.1+ carry the sentinel and are immune to re-migration. A user who
    deliberately sets False after the v0.17.0 ask-first UI ships will have
    the sentinel present and their choice will be preserved.

    The check + migration + write happen inside the same read() invocation —
    a two-pass design (read → check → migrate → re-read) opens a window
    where a deliberate user choice could be clobbered by an interleaved read.
    """
    path = _get_prefs_path(vault_path)
    # safe_read_json returns {} silently for a missing file (first run) and
    # logs + returns {} on a corrupt file (ADR-039).
    stored = safe_read_json(path, default={})
    if not isinstance(stored, dict):
        stored = {}

    # Atomic migration: gated on the schema-version sentinel so this fires
    # at most once per vault. The check inspects the in-memory stored dict
    # before any caller sees the value.
    if (
        stored.get("web_search_autonomous") is False
        and "prefs_schema_version" not in stored
    ):
        stored["web_search_autonomous"] = True
        stored["prefs_schema_version"] = 1
        # S9: skip the write attempt if a prior write already failed
        # permanently for this path. The in-memory migration still applies,
        # but we stop hammering the disk and flooding logs.
        if path in _migration_write_failed_paths:
            logger.debug(
                "[PREFERENCES] Skipping migration write for %s (prior failure)",
                path.name,
            )
        else:
            try:
                safe_write_json(path, stored)
                # Only log the success message after the write actually
                # succeeded -- avoids the misleading "one-shot" claim when
                # the write later raises.
                logger.info(
                    "[PREFERENCES] Migrated v0.15.x web_search_autonomous=False -> True (one-shot, sentinel set)"
                )
                # In case this path was previously failing and the underlying
                # condition cleared, stop suppressing future attempts.
                _migration_write_failed_paths.discard(path)
            except JsonIoError as exc:
                logger.warning(
                    "[PREFERENCES] Migration write failed for %s: %s", path.name, exc
                )
                _migration_write_failed_paths.add(path)

    return {**PREFERENCE_DEFAULTS, **stored}


def get(key: str, default=None, vault_path: Path | None = None):
    """Get a single preference value, with a default if not set."""
    return read(vault_path).get(key, default)


def write(key: str, value, vault_path: Path | None = None) -> None:
    """Set a single preference value. Creates the file if needed."""
    path = _get_prefs_path(vault_path)
    prefs = read(vault_path)
    prefs[key] = value
    safe_write_json(path, prefs)


def update(updates: dict, vault_path: Path | None = None) -> None:
    """Update multiple preference values at once."""
    path = _get_prefs_path(vault_path)
    prefs = read(vault_path)
    prefs.update(updates)
    safe_write_json(path, prefs)
