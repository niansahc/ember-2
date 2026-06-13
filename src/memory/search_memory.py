from src.core.config import get_private_vault_path
from src.core.jsonio import JsonIoError
from src.memory.storage import MemoryStorage


storage = MemoryStorage()


def search_memories(query: str, memory_type: str = "journal", limit: int = 5):
    vault = get_private_vault_path()
    memory_dir = storage.get_memory_dir(vault, memory_type)

    files = storage.list_memory_files(memory_dir)

    query_words = set(query.lower().split())
    scored_results = []

    for file_path in files:
        try:
            memory = storage.read_json(file_path)
        except JsonIoError:
            # Skip a single corrupt/unreadable record (ADR-039); logged by
            # safe_read_json.
            continue
        # Skip suppressed records (junk flagged by audit tools)
        metadata = memory.get("metadata", {})
        if isinstance(metadata, dict) and metadata.get("quality") == "suppressed":
            continue
        text = memory.get("text", "").lower()
        text_words = set(text.split())

        overlap = len(query_words.intersection(text_words))

        if overlap > 0:
            scored_results.append((overlap, memory))

    scored_results.sort(key=lambda x: x[0], reverse=True)

    return [memory for _, memory in scored_results[:limit]]