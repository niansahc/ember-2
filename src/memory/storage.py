"""
src/memory/storage.py

Low-level vault storage for Ember-2 memory records.

All memory writes flow through MemoryStorage.get_memory_dir() which
validates the memory_type against VALID_MEMORY_TYPES. This is the
single enforcement point for typed memory — any invalid type raises
a ValueError before a directory is created or a file is written.
"""

import json
from pathlib import Path


# Canonical list of valid memory types. Any write to the vault must use
# one of these. This matches the taxonomy in CLAUDE.md § Memory Record Schema.
# The state layer has its own validation via VALID_STATE_CATEGORIES — both
# use "state" as a type, but state records are additionally validated by
# StateRecord.__post_init__().
VALID_MEMORY_TYPES: frozenset[str] = frozenset({
    "profile",
    "journal",
    "conversation",
    "reflection",
    "summary",
    "state",
    "task",
    "project",
    "reference",
    "ingested",
    "archive",
    "system_event",
    "decision",
    "review_log",
    "evaluation",
    "session",
})


class MemoryStorage:
    def get_memory_dir(self, vault_path: Path, memory_type: str) -> Path:
        """
        Return the directory for a memory type, creating it if needed.

        Validates that memory_type is in VALID_MEMORY_TYPES before creating
        or returning the directory. This prevents typos from creating arbitrary
        folders and ensures all vault records are typed correctly.

        Raises
        ------
        ValueError
            If memory_type is not in VALID_MEMORY_TYPES.
        """
        if memory_type not in VALID_MEMORY_TYPES:
            raise ValueError(
                f"Invalid memory type '{memory_type}'. "
                f"Must be one of: {sorted(VALID_MEMORY_TYPES)}"
            )
        memory_dir = vault_path / "memory" / memory_type
        memory_dir.mkdir(parents=True, exist_ok=True)
        return memory_dir

    def write_json(self, file_path: Path, data: dict):
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def read_json(self, file_path: Path) -> dict:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_memory_files(self, memory_dir: Path):
        return sorted(memory_dir.glob("*.json"), reverse=True)
