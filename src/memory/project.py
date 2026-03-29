"""
src/memory/project.py

Project management for conversation grouping.

Projects are stored as append-only records in memory/project/.
Multiple records can exist per project_id (after renames, recolors, deletes).
Resolution: latest timestamp wins per project_id.

A project record looks like:
{
  "id": "2026-03-24T20-00-00",
  "timestamp": "...",
  "type": "project",
  "text": "Ember Development",
  "source": "api",
  "tags": ["project"],
  "metadata": {
    "project_id": "proj_abc123",
    "color": "#ff8c00",
    "deleted": false
  }
}
"""

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.core.config import get_private_vault_path
from src.memory.storage import MemoryStorage

logger = logging.getLogger("ember.project")

storage = MemoryStorage()


def _project_dir() -> Path:
    """Return the project storage directory, creating it if needed."""
    return storage.get_memory_dir(get_private_vault_path(), "project")


def _now_id() -> str:
    """Generate a timestamp-based ID."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S-%f")


def _generate_project_id() -> str:
    """Generate a unique project ID."""
    return f"proj_{uuid.uuid4().hex[:12]}"


def _read_all_project_records() -> list[dict]:
    """Read every JSON file in memory/project/."""
    records = []
    for f in storage.list_memory_files(_project_dir()):
        try:
            records.append(storage.read_json(f))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping corrupt project file %s: %s", f, e)
    return records


def _resolve_projects(records: list[dict]) -> dict[str, dict]:
    """
    Given all project records, return a dict of project_id -> latest record.
    Latest = highest timestamp wins.
    """
    resolved: dict[str, dict] = {}
    for rec in records:
        pid = rec.get("metadata", {}).get("project_id", "")
        if not pid:
            continue
        existing = resolved.get(pid)
        if existing is None or rec.get("timestamp", "") > existing.get("timestamp", ""):
            resolved[pid] = rec
    return resolved


def list_projects() -> list[dict]:
    """
    List all active projects.
    Returns list of {id, name, color, created_at}.
    """
    all_records = _read_all_project_records()
    resolved = _resolve_projects(all_records)

    projects = []
    for pid, rec in resolved.items():
        if rec.get("metadata", {}).get("deleted", False):
            continue
        projects.append({
            "id": pid,
            "name": rec.get("text", "Untitled"),
            "color": rec.get("metadata", {}).get("color", "#ff8c00"),
            "created_at": rec.get("metadata", {}).get("created_at", rec.get("timestamp", "")),
        })

    # Sort by name
    projects.sort(key=lambda p: p["name"].lower())
    return projects


def get_project(project_id: str) -> Optional[dict]:
    """Get the resolved (latest) project record for a project_id."""
    all_records = _read_all_project_records()
    resolved = _resolve_projects(all_records)
    rec = resolved.get(project_id)
    if rec is None or rec.get("metadata", {}).get("deleted", False):
        return None
    return rec


def create_project(name: str, color: str = "#ff8c00") -> dict:
    """
    Create a new project. Returns {id, name, color}.
    """
    project_id = _generate_project_id()
    now = datetime.now(timezone.utc)
    record = {
        "id": _now_id(),
        "timestamp": now.isoformat(),
        "type": "project",
        "text": name,
        "source": "api",
        "tags": ["project"],
        "metadata": {
            "project_id": project_id,
            "color": color,
            "created_at": now.isoformat(),
            "deleted": False,
        },
    }
    file_path = _project_dir() / f"{record['id']}.json"
    storage.write_json(file_path, record)
    logger.info("Created project %s: %s", project_id, name)
    return {"id": project_id, "name": name, "color": color}


def update_project(project_id: str, name: Optional[str] = None, color: Optional[str] = None) -> Optional[Path]:
    """
    Update a project's name and/or color by writing a new record.
    Append-only: the old record remains untouched.
    """
    existing = get_project(project_id)
    if existing is None:
        return None

    new_name = name if name is not None else existing.get("text", "Untitled")
    new_color = color if color is not None else existing.get("metadata", {}).get("color", "#ff8c00")

    now = datetime.now(timezone.utc)
    record = {
        "id": _now_id(),
        "timestamp": now.isoformat(),
        "type": "project",
        "text": new_name,
        "source": "api",
        "tags": ["project", "updated"],
        "metadata": {
            "project_id": project_id,
            "color": new_color,
            "created_at": existing.get("metadata", {}).get("created_at", now.isoformat()),
            "deleted": False,
        },
    }
    file_path = _project_dir() / f"{record['id']}.json"
    storage.write_json(file_path, record)
    logger.info("Updated project %s: name=%s color=%s", project_id, new_name, new_color)
    return file_path


def delete_project(project_id: str) -> Optional[Path]:
    """
    Soft-delete a project by writing a new record with deleted: true.
    Append-only: the old record remains untouched.
    """
    existing = get_project(project_id)
    if existing is None:
        return None

    now = datetime.now(timezone.utc)
    record = {
        "id": _now_id(),
        "timestamp": now.isoformat(),
        "type": "project",
        "text": existing.get("text", "Untitled"),
        "source": "api",
        "tags": ["project", "deleted"],
        "metadata": {
            "project_id": project_id,
            "color": existing.get("metadata", {}).get("color", "#ff8c00"),
            "created_at": existing.get("metadata", {}).get("created_at", now.isoformat()),
            "deleted": True,
        },
    }
    file_path = _project_dir() / f"{record['id']}.json"
    storage.write_json(file_path, record)
    logger.info("Soft-deleted project %s", project_id)
    return file_path
