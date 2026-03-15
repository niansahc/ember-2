from src.core.config import get_private_vault_path
from src.memory.storage import MemoryStorage


storage = MemoryStorage()


def search_memories(query: str, memory_type: str = "journal", limit: int = 5):
    vault = get_private_vault_path()
    memory_dir = storage.get_memory_dir(vault, memory_type)

    files = storage.list_memory_files(memory_dir)

    query_words = set(query.lower().split())
    scored_results = []

    for file_path in files:
        memory = storage.read_json(file_path)
        text = memory.get("text", "").lower()
        text_words = set(text.split())

        overlap = len(query_words.intersection(text_words))

        if overlap > 0:
            scored_results.append((overlap, memory))

    scored_results.sort(key=lambda x: x[0], reverse=True)

    return [memory for _, memory in scored_results[:limit]]