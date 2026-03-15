from src.core.config import get_private_vault_path
from src.memory.storage import MemoryStorage


storage = MemoryStorage()


def search_memories(query: str, memory_type: str = "journal", limit: int = 5):
    vault = get_private_vault_path()
    memory_dir = storage.get_memory_dir(vault, memory_type)

    files = storage.list_memory_files(memory_dir)

    results = []

    for file_path in files:
        memory = storage.read_json(file_path)
        text = memory.get("text", "")

        if query.lower() in text.lower():
            results.append(memory)

        if len(results) >= limit:
            break

    return results