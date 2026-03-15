import json
from pathlib import Path


class MemoryStorage:

    def get_memory_dir(self, vault_path: Path, memory_type: str) -> Path:
        memory_dir = vault_path / f"{memory_type}s"
        memory_dir.mkdir(parents=True, exist_ok=True)
        return memory_dir

    def write_json(self, file_path: Path, data: dict):
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def read_json(self, file_path: Path) -> dict:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def list_memory_files(self, memory_dir: Path):
        return sorted(memory_dir.glob("*.json"), reverse=True)
    