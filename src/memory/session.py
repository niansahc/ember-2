"""
src/memory/session.py

Session management for conversation grouping.

Sessions are stored as append-only records in memory/session/.
Multiple records can exist per session_id (after renames, deletes).
Resolution: latest timestamp wins per session_id.

Conversation turns reference their session via metadata.session_id.
updated_at and turn_count are derived from conversation records at
read time — never stored on the session record.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.core.config import get_private_vault_path
from src.memory.storage import MemoryStorage

logger = logging.getLogger("ember.session")

storage = MemoryStorage()


def _session_dir() -> Path:
    """Return the session storage directory, creating it if needed."""
    return storage.get_memory_dir(get_private_vault_path(), "session")


def _conversation_dir() -> Path:
    """Return the conversation storage directory."""
    return storage.get_memory_dir(get_private_vault_path(), "conversation")


def _now_id() -> str:
    """Generate a timestamp-based ID."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _read_all_session_records() -> list[dict]:
    """Read every JSON file in memory/session/."""
    records = []
    for f in storage.list_memory_files(_session_dir()):
        try:
            records.append(storage.read_json(f))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping corrupt session file %s: %s", f, e)
    return records


def _resolve_sessions(records: list[dict]) -> dict[str, dict]:
    """
    Given all session records, return a dict of session_id -> latest record.
    Latest = highest timestamp wins.
    """
    resolved: dict[str, dict] = {}
    for rec in records:
        sid = rec.get("metadata", {}).get("session_id", rec.get("id", ""))
        existing = resolved.get(sid)
        if existing is None or rec.get("timestamp", "") > existing.get("timestamp", ""):
            resolved[sid] = rec
    return resolved


def _scan_conversation_turns() -> dict[str, list[dict]]:
    """
    Scan all conversation records, group by session_id.
    Returns {session_id: [turn, turn, ...]} sorted by timestamp.
    """
    grouped: dict[str, list[dict]] = {}
    for f in storage.list_memory_files(_conversation_dir()):
        try:
            rec = storage.read_json(f)
        except (json.JSONDecodeError, OSError):
            continue
        sid = rec.get("metadata", {}).get("session_id")
        if sid:
            grouped.setdefault(sid, []).append(rec)
    # Sort each group by timestamp ascending
    for sid in grouped:
        grouped[sid].sort(key=lambda r: r.get("timestamp", ""))
    return grouped


def list_sessions(limit: int = 50) -> list[dict]:
    """
    List all active sessions, newest first.
    Derives updated_at and turn_count from conversation records.
    """
    all_records = _read_all_session_records()
    resolved = _resolve_sessions(all_records)
    turn_groups = _scan_conversation_turns()

    sessions = []
    for sid, rec in resolved.items():
        # Skip soft-deleted sessions
        if rec.get("metadata", {}).get("deleted", False):
            continue

        turns = turn_groups.get(sid, [])
        turn_count = len(turns)
        created_at = rec.get("metadata", {}).get("created_at", rec.get("timestamp", ""))

        if turns:
            updated_at = turns[-1].get("timestamp", created_at)
        else:
            updated_at = created_at

        sessions.append({
            "id": sid,
            "title": rec.get("text", "Untitled"),
            "created_at": created_at,
            "updated_at": updated_at,
            "turn_count": turn_count,
        })

    # Sort by updated_at descending
    sessions.sort(key=lambda s: s["updated_at"], reverse=True)
    return sessions[:limit]


def get_session(session_id: str) -> Optional[dict]:
    """Get the resolved (latest) session record for a session_id."""
    all_records = _read_all_session_records()
    resolved = _resolve_sessions(all_records)
    rec = resolved.get(session_id)
    if rec is None or rec.get("metadata", {}).get("deleted", False):
        return None
    return rec


def get_turns(session_id: str, limit: int = 200) -> list[dict]:
    """Get all conversation turns for a session, oldest first."""
    turns = []
    for f in storage.list_memory_files(_conversation_dir()):
        try:
            rec = storage.read_json(f)
        except (json.JSONDecodeError, OSError):
            continue
        if rec.get("metadata", {}).get("session_id") == session_id:
            turns.append(rec)
    turns.sort(key=lambda r: r.get("timestamp", ""))
    return turns[:limit]


def session_exists(session_id: str) -> bool:
    """Check whether any session record exists for this session_id."""
    for f in storage.list_memory_files(_session_dir()):
        try:
            rec = storage.read_json(f)
            if rec.get("metadata", {}).get("session_id") == session_id:
                return True
        except (json.JSONDecodeError, OSError):
            continue
    return False


def create_session(session_id: str, title: str) -> Path:
    """Write a new session record. Returns the file path."""
    now = datetime.now(timezone.utc)
    record = {
        "id": _now_id(),
        "timestamp": now.isoformat(),
        "type": "session",
        "text": title,
        "source": "api",
        "tags": ["session"],
        "metadata": {
            "session_id": session_id,
            "created_at": now.isoformat(),
            "deleted": False,
        },
    }
    file_path = _session_dir() / f"{record['id']}.json"
    storage.write_json(file_path, record)
    logger.info("Created session %s: %s", session_id, title)
    return file_path


def rename_session(session_id: str, new_title: str) -> Optional[Path]:
    """
    Rename a session by writing a new record with the updated title.
    Append-only: the old record remains untouched.
    """
    existing = get_session(session_id)
    if existing is None:
        return None

    now = datetime.now(timezone.utc)
    record = {
        "id": _now_id(),
        "timestamp": now.isoformat(),
        "type": "session",
        "text": new_title,
        "source": "api",
        "tags": ["session", "renamed"],
        "metadata": {
            "session_id": session_id,
            "created_at": existing.get("metadata", {}).get("created_at", now.isoformat()),
            "deleted": False,
        },
    }
    file_path = _session_dir() / f"{record['id']}.json"
    storage.write_json(file_path, record)
    logger.info("Renamed session %s -> %s", session_id, new_title)
    return file_path


def delete_session(session_id: str) -> Optional[Path]:
    """
    Soft-delete a session by writing a new record with deleted: true.
    Append-only: the old record remains untouched.
    """
    existing = get_session(session_id)
    if existing is None:
        return None

    now = datetime.now(timezone.utc)
    record = {
        "id": _now_id(),
        "timestamp": now.isoformat(),
        "type": "session",
        "text": existing.get("text", "Untitled"),
        "source": "api",
        "tags": ["session", "deleted"],
        "metadata": {
            "session_id": session_id,
            "created_at": existing.get("metadata", {}).get("created_at", now.isoformat()),
            "deleted": True,
        },
    }
    file_path = _session_dir() / f"{record['id']}.json"
    storage.write_json(file_path, record)
    logger.info("Soft-deleted session %s", session_id)
    return file_path
