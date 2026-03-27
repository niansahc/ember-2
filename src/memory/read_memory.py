from __future__ import annotations

from datetime import datetime, timezone

from src.core.config import get_private_vault_path
from src.memory.storage import MemoryStorage


storage = MemoryStorage()


def read_memories(memory_type: str = "journal", limit: int = 5):
    vault = get_private_vault_path()
    memory_dir = storage.get_memory_dir(vault, memory_type)

    files = storage.list_memory_files(memory_dir)

    memories = []

    for file_path in files:
        memory = storage.read_json(file_path)
        # Skip suppressed records (junk flagged by audit tools)
        metadata = memory.get("metadata", {})
        if isinstance(metadata, dict) and metadata.get("quality") == "suppressed":
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

    try:
        return float(timestamp)
    except (TypeError, ValueError):
        pass

    try:
        normalized = str(timestamp).replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return 0.0
    