from src.context.models import ContextItem
from src.memory.service import MemoryService
from src.memory.search_conversation import search_conversation_memories


class ContextRetriever:
    def __init__(self, memory_service: MemoryService | None = None):
        self.memory_service = memory_service or MemoryService()

    def get_memory_items(self, user_message: str) -> list[ContextItem]:
        from src.retrieval.semantic_search import semantic_search

        results = semantic_search(user_message, limit=5)
        items: list[ContextItem] = []

        for result in results:
            metadata = result.get("metadata", {})
            content = result.get("content", "")

            items.append(
                ContextItem(
                    id=metadata.get("chunk_id", result.get("path", "")),
                    content=content,
                    source=result.get("memory_type", "memory"),
                    item_type=result.get("memory_type", "memory"),
                    score=result.get("score", 0.0),
                    timestamp=metadata.get("created_at"),
                    tags=metadata.get("tags", []),
                    metadata={
                        **metadata,
                        "path": result.get("path"),
                        "memory_type": result.get("memory_type"),
                    },
                )
            )

        return items

    def get_reflection_items(self, user_message: str) -> list[ContextItem]:
        results = self.memory_service.search(user_message, memory_type="reflection", limit=3)

        if not results:
            results = self.memory_service.read(memory_type="reflection", limit=1)

        items: list[ContextItem] = []

        for r in results:
            items.append(
                ContextItem(
                    id=r.get("id", ""),
                    content=r.get("text", ""),
                    source="reflection",
                    item_type="reflection",
                    score=1.0,
                    timestamp=r.get("timestamp"),
                    tags=r.get("tags", []),
                    metadata=r,
                )
            )

        return items

    def get_conversation_items(self, user_message: str) -> list[ContextItem]:
        results = search_conversation_memories(user_message, top_k=3)
        items: list[ContextItem] = []

        for result in results:
            memory = result.get("memory", {})

            items.append(
                ContextItem(
                    id=memory.get("timestamp", ""),
                    content=memory.get("text", ""),
                    source="conversation",
                    item_type="conversation",
                    score=result.get("score", 0.0),
                    timestamp=memory.get("timestamp"),
                    tags=memory.get("tags", []),
                    metadata=memory,
                )
            )

        return items

    def retrieve(self, user_message: str) -> tuple[list[ContextItem], list[ContextItem]]:
        memory_items = self.get_memory_items(user_message)
        conversation_items = self.get_conversation_items(user_message)
        reflection_items = self.get_reflection_items(user_message)

        memory_items.extend(conversation_items)
        return memory_items, reflection_items