"""
src/state/state_service.py

StateService handles reading and writing state records to the private vault.

Vault location: private_vault/memory/state/
Filename convention: {timestamp}_{type}.json
  e.g. "2026-03-21T14-30-00_current_focus.json"

Design rules (per CLAUDE.md):
  - Append-only. Existing files are never modified or deleted.
  - "Current state" is resolved by StateResolver (not here) by
    selecting the latest record(s) per category.
  - Corrupted or unreadable JSON files are skipped with a warning;
    they never crash the service.
  - The vault directory is created on first write if it does not exist.
"""

from __future__ import annotations

import json
import warnings
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from src.core.config import get_private_vault_path
from src.state.models import VALID_STATE_CATEGORIES, StateRecord


# Subdirectory within the vault where state records live.
STATE_MEMORY_SUBDIR = "memory/state"


class StateService:
    """
    Reads and writes StateRecord objects to private_vault/memory/state/.

    This service is purely responsible for vault I/O. It does not resolve
    "current state" (that is StateResolver's job) and does not interact
    with the context layer directly.

    Usage
    -----
    service = StateService()

    # Write a new state record
    path = service.write(StateRecord(
        id="2026-03-21T14-30-00",
        timestamp="2026-03-21T14-30-00",
        type="current_focus",
        text="Building the state layer for Ember-2",
        source="user_input",
    ))

    # Read all state records (newest first)
    records = service.read_all()

    # Read only records of a specific category
    focus_records = service.read_by_category("current_focus")
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

    def _get_state_dir(self) -> Path:
        """Return the state memory directory, creating it if needed."""
        vault = self._vault_path or get_private_vault_path()
        state_dir = vault / STATE_MEMORY_SUBDIR
        state_dir.mkdir(parents=True, exist_ok=True)
        return state_dir

    def _record_to_dict(self, record: StateRecord) -> dict:
        """Serialise a StateRecord to a plain dict for JSON storage."""
        return asdict(record)

    def _dict_to_record(self, data: dict, file_path: Path) -> StateRecord | None:
        """
        Deserialise a dict loaded from JSON into a StateRecord.

        Returns None (and logs a warning) if the data is missing required
        fields or contains an unrecognised state type.
        """
        required = {"id", "timestamp", "type", "text", "source"}
        missing = required - data.keys()

        if missing:
            warnings.warn(
                f"[STATE_SERVICE] Skipping {file_path.name}: "
                f"missing required fields {missing}",
                stacklevel=2,
            )
            return None

        state_type = data.get("type", "")

        if state_type not in VALID_STATE_CATEGORIES:
            warnings.warn(
                f"[STATE_SERVICE] Skipping {file_path.name}: "
                f"unrecognised state type '{state_type}'",
                stacklevel=2,
            )
            return None

        return StateRecord(
            id=data["id"],
            timestamp=data["timestamp"],
            type=state_type,
            text=data["text"],
            source=data["source"],
            tags=data.get("tags", []),
            metadata=data.get("metadata", {}),
        )

    def _filename_for(self, record: StateRecord) -> str:
        """
        Build the filename for a StateRecord.

        Convention: {timestamp}_{type}.json
        Colons in the timestamp are replaced with hyphens so the filename
        is safe on all platforms (Windows disallows colons in filenames).
        e.g. "2026-03-21T14-30-00_current_focus.json"
        """
        safe_timestamp = record.timestamp.replace(":", "-")
        return f"{safe_timestamp}_{record.type}.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def write(self, record: StateRecord) -> Path:
        """
        Write a StateRecord to the vault as a JSON file.

        The record is validated by StateRecord.__post_init__ before this
        method is called (via the dataclass constructor), so type safety
        is guaranteed at the model level.

        This method is append-only: it will never overwrite an existing
        file. If a file with the same name already exists, a warning is
        issued and the write is skipped.

        Parameters
        ----------
        record : StateRecord
            The state artifact to persist.

        Returns
        -------
        Path
            The path of the written (or already-existing) file.
        """
        state_dir = self._get_state_dir()
        filename = self._filename_for(record)
        file_path = state_dir / filename

        if file_path.exists():
            warnings.warn(
                f"[STATE_SERVICE] File already exists, skipping write: {filename}",
                stacklevel=2,
            )
            return file_path

        data = self._record_to_dict(record)

        with file_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        return file_path

    def read_all(self) -> list[StateRecord]:
        """
        Read all state records from the vault, newest first.

        Corrupted or unreadable JSON files are skipped with a warning.
        Records with unrecognised types are also skipped.

        Returns
        -------
        list[StateRecord]
            All valid state records, sorted by timestamp descending.
        """
        state_dir = self._get_state_dir()

        # list_files newest-first by filename (timestamps sort lexicographically)
        json_files = sorted(state_dir.glob("*.json"), reverse=True)

        records: list[StateRecord] = []

        for file_path in json_files:
            try:
                with file_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                warnings.warn(
                    f"[STATE_SERVICE] Skipping unreadable file {file_path.name}: {exc}",
                    stacklevel=2,
                )
                continue

            record = self._dict_to_record(data, file_path)

            if record is not None:
                records.append(record)

        return records

    def read_by_category(self, category: str) -> list[StateRecord]:
        """
        Read all state records matching a specific category, newest first.

        Parameters
        ----------
        category : str
            A valid state category from VALID_STATE_CATEGORIES,
            e.g. "current_focus", "blocker", "open_loop".

        Returns
        -------
        list[StateRecord]
            All valid records of that category, sorted newest first.

        Raises
        ------
        ValueError
            If the requested category is not in VALID_STATE_CATEGORIES.
        """
        if category not in VALID_STATE_CATEGORIES:
            raise ValueError(
                f"Unknown state category '{category}'. "
                f"Must be one of: {sorted(VALID_STATE_CATEGORIES)}"
            )

        return [r for r in self.read_all() if r.type == category]

    @staticmethod
    def make_record(
        state_type: str,
        text: str,
        source: str = "user_input",
        tags: list[str] | None = None,
        metadata: dict | None = None,
    ) -> StateRecord:
        """
        Convenience factory that builds a StateRecord with an auto-generated
        timestamp and id.

        Parameters
        ----------
        state_type : str
            The state category (must be in VALID_STATE_CATEGORIES).
        text : str
            Human-readable description of the state artifact.
        source : str
            The subsystem creating this record. Defaults to "user_input".
        tags : list[str] | None
            Optional labels. Defaults to an empty list.
        metadata : dict | None
            Optional structured context. Defaults to an empty dict.

        Returns
        -------
        StateRecord
            A fully populated, validated StateRecord ready to be written.
        """
        # NOTE: hyphens are used as time separators (%H-%M-%S) instead of colons
        # (%H:%M:%S) because Windows disallows colons in filenames. This deviates
        # from strict ISO 8601 (which uses colons). Any code that parses these
        # timestamps — particularly timeline queries and StateResolver sort logic —
        # must handle this format, e.g. by calling .replace("-", ":") on the time
        # portion before passing to datetime.fromisoformat().
        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")

        return StateRecord(
            id=timestamp,
            timestamp=timestamp,
            type=state_type,
            text=text,
            source=source,
            tags=tags or [],
            metadata=metadata or {},
        )
