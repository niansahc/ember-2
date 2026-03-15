from src.memory.write_memory import write_memory
from src.memory.read_memory import read_memories
from src.memory.search_memory import search_memories
from src.memory.read_memory import read_memories

class MemoryService:

    def write(
        self,
        text: str,
        memory_type: str = "journal",
        source: str = "api",
        tags=None,
        metadata: dict | None = None,
    ):

        existing = read_memories(memory_type, limit=10)

        normalized = text.strip().lower()

        for mem in existing:
            if mem.get("text", "").strip().lower() == normalized:
                return {"status": "duplicate_skipped"}

        return write_memory(text, memory_type, source, tags, metadata)


    def read(self, memory_type: str = "journal", limit: int = 5):
        return read_memories(memory_type, limit)

    def search(self, query: str, memory_type: str = "journal", limit: int = 5):
        return search_memories(query, memory_type, limit)