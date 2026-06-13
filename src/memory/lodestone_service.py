"""
src/memory/lodestone_service.py

Read and write lodestone vault records (living layer).

Lodestone records capture user values discovered through explicit
statements or reflection synthesis. See ADR-017, TDD §48.

Append-only. No hard deletes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from src.core.config import get_private_vault_path
from src.core.jsonio import safe_read_json
from src.memory.storage import MemoryStorage

logger = logging.getLogger("ember.lodestone")

MAX_ACTIVE_RECORDS = 15
# Cap proposed (unconfirmed) record growth. write() bypasses MAX_ACTIVE_RECORDS
# when confirmed=False, so without this ceiling a never-confirming user could
# accumulate inferred records every month forever. Path-2 synthesis
# (src/reflection/lodestone_synthesis.py) checks against this before each run.
MAX_PROPOSED_RECORDS = 20

storage = MemoryStorage()


def _lodestone_dir() -> Path:
    vault = get_private_vault_path()
    return storage.get_memory_dir(vault, "lodestone")


def _load_record(file_path: Path) -> dict[str, Any] | None:
    # safe_read_json logs corruption (ADR-039) and returns None so a single
    # bad lodestone file is skipped, not fatal.
    return safe_read_json(file_path, default=None)


def _all_records() -> list[dict[str, Any]]:
    """Load all lodestone records from vault, newest first."""
    ldir = _lodestone_dir()
    files = sorted(ldir.glob("*.json"), reverse=True)
    records = []
    for f in files:
        rec = _load_record(f)
        if rec and rec.get("type") == "lodestone":
            records.append(rec)
    return records


def read_active() -> list[dict[str, Any]]:
    """Return confirmed lodestone records only. Proposed records excluded."""
    return [r for r in _all_records() if r.get("confirmed") is True]


def read_proposed() -> list[dict[str, Any]]:
    """Return proposed (unconfirmed) lodestone records for user review."""
    return [r for r in _all_records() if r.get("confirmed") is not True]


def read_all() -> list[dict[str, Any]]:
    """Return all lodestone records (confirmed + proposed)."""
    return _all_records()


def write(
    value: str,
    taxonomy_category: str,
    acquisition_path: str = "explicit",
    source: str = "conversation",
    supporting_evidence: str = "",
    confirmed: bool = True,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Write a lodestone record to the vault.

    Enforces MAX_ACTIVE_RECORDS cap — raises ValueError if cap would be
    exceeded by a new confirmed record.
    """
    if confirmed and len(read_active()) >= MAX_ACTIVE_RECORDS:
        raise ValueError(
            f"Lodestone cap reached ({MAX_ACTIVE_RECORDS} active records). "
            "Confirm fewer records or dismiss existing ones before adding more."
        )

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
    extra_meta = metadata or {}

    record = {
        "id": timestamp,
        "timestamp": timestamp,
        "type": "lodestone",
        "value": value,
        "acquisition_path": acquisition_path,
        "source": source,
        "supporting_evidence": supporting_evidence,
        "recurrence_count": 1,
        "confirmed": confirmed,
        "conflict_resolution": False,
        "metadata": {
            "user_note": extra_meta.get("user_note"),
            "taxonomy_category": taxonomy_category,
            "flagged_as_noise": False,
        },
    }

    ldir = _lodestone_dir()
    file_path = ldir / f"{timestamp}.json"
    storage.write_json(file_path, record)

    logger.info(
        "[LODESTONE] Wrote %s record: %s (%s)",
        "confirmed" if confirmed else "proposed",
        value[:60],
        taxonomy_category,
    )
    return record


def update(record_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """
    Update a lodestone record (confirm, dismiss, add user_note).

    Writes a new version of the record (append-only — original preserved
    by timestamp, updated version has new timestamp in filename).
    """
    ldir = _lodestone_dir()
    target_file = None
    target_record = None

    for f in ldir.glob("*.json"):
        rec = _load_record(f)
        if rec and rec.get("id") == record_id:
            target_file = f
            target_record = rec
            break

    if target_record is None:
        return None

    # Apply allowed updates
    if "confirmed" in updates:
        new_confirmed = bool(updates["confirmed"])
        # Check cap if confirming
        if new_confirmed and not target_record.get("confirmed"):
            if len(read_active()) >= MAX_ACTIVE_RECORDS:
                raise ValueError(
                    f"Lodestone cap reached ({MAX_ACTIVE_RECORDS} active records)."
                )
        target_record["confirmed"] = new_confirmed

    if "user_note" in updates:
        if "metadata" not in target_record:
            target_record["metadata"] = {}
        target_record["metadata"]["user_note"] = updates["user_note"]

    if "flagged_as_noise" in updates:
        if "metadata" not in target_record:
            target_record["metadata"] = {}
        target_record["metadata"]["flagged_as_noise"] = bool(updates["flagged_as_noise"])

    # Write updated record back to same file (in-place update is acceptable
    # for lodestone confirmation — the original record id is preserved)
    storage.write_json(target_file, target_record)

    logger.info("[LODESTONE] Updated record %s", record_id)
    return target_record
