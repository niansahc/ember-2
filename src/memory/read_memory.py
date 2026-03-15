from src.core.config import get_private_vault_path
from src.memory.storage import MemoryStorage


storage = MemoryStorage()


def read_memories(memory_type: str = "journal", limit: int = 5):
    vault = get_private_vault_path()
    memory_dir = storage.get_memory_dir(vault, memory_type)

    files = storage.list_memory_files(memory_dir)[:limit]

    memories = []

    for file_path in files:
        memory = storage.read_json(file_path)
        memories.append(memory)

    return memories