from __future__ import annotations

from src.core.config import get_private_vault_path
from src.core.jsonio import JsonIoError
from src.core.timestamps import parse_vault_timestamp
from src.memory.storage import MemoryStorage


storage = MemoryStorage()


def read_memories(
    memory_type: str = "journal",
    limit: int | None = 5,
    session_id: str | None = None,
):
    """Read records of one memory type, newest first.

    limit=None returns every record. session_id keeps only records whose
    metadata.session_id matches, and the limit counts only those. Slicing
    first and filtering second silently drops older matches
    (ultrareview #280), so a caller that filters on anything else must
    pass limit=None and apply its own limit after its filter.
    """
    vault = get_private_vault_path()
    memory_dir = storage.get_memory_dir(vault, memory_type)

    files = storage.list_memory_files(memory_dir)

    memories = []

    for file_path in files:
        try:
            memory = storage.read_json(file_path)
        except JsonIoError:
            # A single corrupt/unreadable record must not crash the whole read
            # (ADR-039). It is logged by safe_read_json; skip it and continue.
            continue
        metadata = memory.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        # Skip suppressed records (junk flagged by audit tools)
        if metadata.get("quality") == "suppressed":
            continue
        if session_id is not None and metadata.get("session_id") != session_id:
            continue
        memories.append(memory)

    memories.sort(key=_memory_sort_key, reverse=True)
    return memories[:limit]


def _memory_sort_key(memory: dict) -> float:
    if not isinstance(memory, dict):
        return 0.0

    timestamp = memory.get("timestamp") or memory.get("created_at")

    if timestamp is None:
        metadata = memory.get("metadata", {})
        if isinstance(metadata, dict):
            timestamp = metadata.get("timestamp") or metadata.get("created_at")

    if timestamp is None:
        return 0.0

    if isinstance(timestamp, (int, float)):
        return float(timestamp)

    # Epoch strings, the vault's hyphenated form and ISO 8601 alike.
    parsed = parse_vault_timestamp(str(timestamp))
    return parsed.timestamp() if parsed is not None else 0.0
