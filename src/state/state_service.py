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
import logging
import warnings
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from src.core.config import get_private_vault_path
from src.state.models import VALID_STATE_CATEGORIES, StateRecord

logger = logging.getLogger("ember.state_service")


# Subdirectory within the vault where state records live.
STATE_MEMORY_SUBDIR = "memory/state"

# Module-level guard against same-second timestamp collisions in
# make_record(). Filename convention is `{timestamp}_{type}.json`, so
# two records of the same type written in the same second collide on
# filename and the second write is silently dropped by the exists-check
# in StateService.write(). This is the same root cause as BUG-005 —
# mirrors the fix in session._now_id(), task_service.next_timestamp(),
# and write_memory._next_timestamp(). Uses second precision (not
# microsecond) to match the existing state layer format, but the spin
# guard ensures no two calls return the same value within a process.
_last_state_id: str = ""


def _next_state_timestamp() -> str:
    """Generate a second-precision timestamp string, guaranteed unique
    per process. Spins on datetime.now() until the result differs from
    the previous return value."""
    global _last_state_id
    while True:
        candidate = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        if candidate != _last_state_id:
            _last_state_id = candidate
            return candidate


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

        # Graceful handling for resolved_X types written by manual cleanup
        # or older code paths. Strip the prefix, validate the underlying
        # category, and force metadata.resolved=True so the resolver skips
        # the record correctly.
        if state_type.startswith("resolved_"):
            underlying = state_type[len("resolved_"):]
            if underlying in VALID_STATE_CATEGORIES:
                state_type = underlying
                meta = data.get("metadata") or {}
                meta["resolved"] = True
                data["metadata"] = meta

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

    def resolve_open_loops_by_topic(self, declined_topic: str) -> int:
        """Resolve open_loop records whose text matches a declined topic.

        BUG-009: when the user explicitly declines a topic, all active
        open_loop records that contain the declined topic text are
        resolved by writing a new record with metadata.resolved=True.
        This is the same append-only resolution pattern used by the
        existing open_loop workflow — no records are modified or deleted.

        Matching is case-insensitive substring. The declined topic is
        typically short (extracted from "I don't want to talk about X"),
        so substring match is appropriate — semantic matching would be
        overkill and add an LLM call.

        Returns the number of open_loop records resolved.
        """
        if not declined_topic or len(declined_topic) < 3:
            return 0

        topic_lower = declined_topic.lower()
        open_loops = self.read_by_category("open_loop")
        resolved_count = 0

        for record in open_loops:
            # Skip already-resolved/deleted records
            if record.metadata and (
                record.metadata.get("resolved") or record.metadata.get("deleted")
            ):
                continue
            if topic_lower in record.text.lower():
                resolution = self.make_record(
                    state_type="open_loop",
                    text=f"[Declined] {record.text}",
                    source="topic_decline",
                    tags=["declined", "auto_resolved"],
                    metadata={
                        "resolved": True,
                        "resolution": "user_declined",
                        "original_id": record.id,
                    },
                )
                self.write(resolution)
                resolved_count += 1

        return resolved_count

    @staticmethod
    def resolved_ids(records: list[StateRecord]) -> set[str]:
        """Compute the set of record ids considered resolved (ADR-038).

        Resolution is derived from append-only data, never from an in-place
        edit. A record id is resolved if either:

          - some record carries metadata.original_id equal to that id (an
            append-only resolution tombstone written by resolve_record), or
          - the record itself carries metadata.resolved == True (back-compat
            with records resolved in place by the pre-ADR-038 code path).

        Callers compute "is this record still active?" as
        `record.id not in resolved_ids(records)`.
        """
        resolved: set[str] = set()
        for r in records:
            meta = r.metadata or {}
            if meta.get("resolved"):
                resolved.add(r.id)
            original_id = meta.get("original_id")
            if original_id:
                resolved.add(original_id)
        return resolved

    def resolve_record(self, record_id: str, *, resolution: str | None = None) -> bool:
        """Resolve a state record append-only by appending a resolution tombstone.

        Finds the record whose id == record_id and, if it is not already
        resolved (per resolved_ids), writes a NEW record of the same category
        carrying metadata.original_id == record_id and metadata.resolved == True.
        The original record file is never modified (CLAUDE.md Rule 3, ADR-038).

        Returns True when a tombstone was written, False when no matching
        record exists or it was already resolved. Idempotent: a second call
        for an already-resolved id is a no-op and writes no duplicate tombstone.
        """
        records = self.read_all()
        target = next((r for r in records if r.id == record_id), None)
        if target is None:
            return False
        if record_id in self.resolved_ids(records):
            return False

        meta: dict = {"resolved": True, "original_id": record_id}
        if resolution:
            meta["resolution"] = resolution
        session_id = (target.metadata or {}).get("session_id")
        if session_id:
            meta["session_id"] = session_id

        tombstone = self.make_record(
            state_type=target.type,
            text=f"[resolved] {target.text}",
            source="state_resolver",
            metadata=meta,
        )
        self.write(tombstone)
        logger.info(
            "[STATE] resolve_record: appended tombstone for %s (type=%s resolution=%s)",
            record_id, target.type, resolution,
        )
        return True

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
        # _next_state_timestamp() spins on collision so two back-to-back
        # calls never return the same second. See module-level comment for
        # rationale and BUG-005 cross-reference.
        timestamp = _next_state_timestamp()

        return StateRecord(
            id=timestamp,
            timestamp=timestamp,
            type=state_type,
            text=text,
            source=source,
            tags=tags or [],
            metadata=metadata or {},
        )
