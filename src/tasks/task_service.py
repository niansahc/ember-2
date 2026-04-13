"""
src/tasks/task_service.py

TaskService handles reading and writing task records to the private vault.

Vault location: private_vault/memory/task/
Filename convention: {timestamp}_{title_slug}.json

Design rules (per CLAUDE.md):
  - Append-only. Existing files are never modified or deleted.
  - Status changes write a new record (same task ID, new timestamp).
  - Corrupted or unreadable JSON files are skipped with a warning.
  - The vault directory is created on first write if it does not exist.
"""

from __future__ import annotations

import json
import re
import warnings
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from src.core.config import get_private_vault_path
from src.tasks.models import VALID_TASK_STATUSES, TaskRecord


# Subdirectory within the vault where task records live.
TASK_MEMORY_SUBDIR = "memory/task"


# Module-level guard against same-microsecond timestamp collisions in
# next_timestamp(). Filename convention is `{timestamp}_{slug}.json`, so
# two timestamps colliding on the same microsecond produce the same path
# and the second write hits TaskService.write()'s "file already exists"
# guard, silently dropping the new record. This was the root cause of
# flaky test_update_status: POST + PATCH within one microsecond on a
# fast machine. Mirrors the same fix in src/memory/session.py:_now_id().
_last_timestamp: str = ""


def next_timestamp() -> str:
    """Generate a microsecond-precision timestamp string, guaranteed
    unique per process.

    Spins on `datetime.now()` until the result differs from the previous
    return value. The spin can never run for longer than one microsecond
    of real time. Used by every code path that writes a TaskRecord —
    make_record() and update_task_status_endpoint() in src/api/main.py.
    """
    global _last_timestamp
    while True:
        candidate = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
        if candidate != _last_timestamp:
            _last_timestamp = candidate
            return candidate


class TaskService:
    """
    Reads and writes TaskRecord objects to private_vault/memory/task/.

    This service is purely responsible for vault I/O. It does not resolve
    active tasks (that is TaskResolver's job) and does not interact with
    the context layer directly.
    """

    def __init__(self, vault_path: Path | None = None) -> None:
        """
        Parameters
        ----------
        vault_path : Path | None
            Override the vault path (used in tests). If None, reads from
            PRIVATE_VAULT_PATH via src.core.config.get_private_vault_path().
        """
        self._vault_path = vault_path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_task_dir(self) -> Path:
        """Return the task memory directory, creating it if needed."""
        vault = self._vault_path or get_private_vault_path()
        task_dir = vault / TASK_MEMORY_SUBDIR
        task_dir.mkdir(parents=True, exist_ok=True)
        return task_dir

    def _record_to_dict(self, record: TaskRecord) -> dict:
        """Serialise a TaskRecord to a plain dict for JSON storage."""
        return asdict(record)

    def _dict_to_record(self, data: dict, file_path: Path) -> TaskRecord | None:
        """
        Deserialise a dict loaded from JSON into a TaskRecord.

        Returns None (and logs a warning) if the data is missing required
        fields or contains invalid values.
        """
        required = {"id", "timestamp", "type", "title", "status", "text", "source"}
        missing = required - data.keys()

        if missing:
            warnings.warn(
                f"[TASK_SERVICE] Skipping {file_path.name}: "
                f"missing required fields {missing}",
                stacklevel=2,
            )
            return None

        if data.get("type") != "task":
            warnings.warn(
                f"[TASK_SERVICE] Skipping {file_path.name}: "
                f"type is '{data.get('type')}', expected 'task'",
                stacklevel=2,
            )
            return None

        status = data.get("status", "")
        if status not in VALID_TASK_STATUSES:
            warnings.warn(
                f"[TASK_SERVICE] Skipping {file_path.name}: "
                f"invalid status '{status}'",
                stacklevel=2,
            )
            return None

        return TaskRecord(
            id=data["id"],
            timestamp=data["timestamp"],
            type=data["type"],
            title=data["title"],
            status=data["status"],
            text=data["text"],
            source=data["source"],
            project_id=data.get("project_id"),
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )

    def _filename_for(self, record: TaskRecord) -> str:
        """
        Build the filename for a TaskRecord.

        Convention: {timestamp}_{title_slug}.json
        Title is slugified to lowercase alphanumeric + hyphens, max 40 chars.
        """
        safe_timestamp = record.timestamp.replace(":", "-")
        slug = re.sub(r"[^a-z0-9]+", "-", record.title.lower()).strip("-")[:40]
        return f"{safe_timestamp}_{slug}.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, record: TaskRecord) -> Path:
        """
        Write a TaskRecord to the vault as a JSON file.

        Append-only: never overwrites an existing file. If a file with the
        same name already exists, a warning is issued and the write is skipped.

        Returns
        -------
        Path
            The path of the written (or already-existing) file.
        """
        task_dir = self._get_task_dir()
        filename = self._filename_for(record)
        file_path = task_dir / filename

        if file_path.exists():
            warnings.warn(
                f"[TASK_SERVICE] File already exists, skipping write: {filename}",
                stacklevel=2,
            )
            return file_path

        data = self._record_to_dict(record)

        with file_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return file_path

    def read_all(self) -> list[TaskRecord]:
        """
        Read all task records from the vault, newest first.

        Corrupted or unreadable JSON files are skipped with a warning.

        Returns
        -------
        list[TaskRecord]
            All valid task records, sorted by timestamp descending.
        """
        task_dir = self._get_task_dir()
        json_files = sorted(task_dir.glob("*.json"), reverse=True)

        records: list[TaskRecord] = []

        for file_path in json_files:
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                warnings.warn(
                    f"[TASK_SERVICE] Skipping unreadable file {file_path.name}: {exc}",
                    stacklevel=2,
                )
                continue

            record = self._dict_to_record(data, file_path)

            if record is not None:
                records.append(record)

        return records

    def read_by_status(self, status: str) -> list[TaskRecord]:
        """
        Read all task records matching a specific status, newest first.

        Raises ValueError if the status is not valid.
        """
        if status not in VALID_TASK_STATUSES:
            raise ValueError(
                f"Unknown task status '{status}'. "
                f"Must be one of: {sorted(VALID_TASK_STATUSES)}"
            )
        return [r for r in self.read_all() if r.status == status]

    def read_by_project(self, project_id: str) -> list[TaskRecord]:
        """
        Read all task records for a specific project, newest first.
        """
        return [r for r in self.read_all() if r.project_id == project_id]

    def read_active(self) -> list[TaskRecord]:
        """
        Read all proposed and active task records, newest first.

        Resolves to latest record per task ID before filtering by status.
        A task that was cancelled or completed is excluded even if an
        earlier record for the same ID had status=active.
        """
        all_records = self.read_all()
        # Resolve to latest per task ID (read_all is newest-first)
        latest_by_id: dict[str, TaskRecord] = {}
        for r in all_records:
            if r.id not in latest_by_id:
                latest_by_id[r.id] = r
        return [r for r in latest_by_id.values() if r.status in {"proposed", "active"}]

    def read_by_id(self, task_id: str) -> TaskRecord | None:
        """
        Find the most recent record for a given task ID.

        Because status updates write new records with the same ID but a new
        timestamp, this returns the newest record matching the ID.

        Returns None if no record with that ID exists.
        """
        matches = [r for r in self.read_all() if r.id == task_id]
        if not matches:
            return None
        # read_all() is already newest-first, so index 0 is the latest
        return matches[0]

    @staticmethod
    def make_record(
        title: str,
        status: str = "active",
        source: str = "user_input",
        project_id: str | None = None,
        text: str | None = None,
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> TaskRecord:
        """
        Convenience factory that builds a TaskRecord with an auto-generated
        timestamp and id.

        Parameters
        ----------
        title : str
            Short task name.
        status : str
            Initial lifecycle state. Defaults to "active".
        source : str
            Subsystem creating this record. Defaults to "user_input".
        project_id : str | None
            Project scope, or None for general tasks.
        text : str | None
            Longer description. Defaults to the title if not provided.
        tags : list[str] | None
            Optional labels.
        metadata : dict | None
            Optional structured context.
        """
        # next_timestamp() spins on collision so two back-to-back calls
        # never share the same microsecond. See module docstring for
        # _last_timestamp and the BUG-005 / test_update_status flake.
        timestamp = next_timestamp()

        return TaskRecord(
            id=timestamp,
            timestamp=timestamp,
            type="task",
            title=title,
            status=status,
            text=text or title,
            source=source,
            project_id=project_id,
            tags=tags or [],
            metadata=metadata or {},
        )
