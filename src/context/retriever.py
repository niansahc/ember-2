import re

from src.context.models import ContextItem
from src.memory.search_conversation import search_conversation_memories
from src.memory.service import MemoryService


class ContextRetriever:
    def __init__(self, memory_service: MemoryService | None = None):
        self.memory_service = memory_service or MemoryService()

    def get_memory_items(self, user_message: str) -> list[ContextItem]:
        from src.retrieval.semantic_search import semantic_search

        results = semantic_search(user_message, limit=8)
        items: list[ContextItem] = []

        for result in results:
            metadata = result.get("metadata", {})
            content = result.get("content", "")

            if self._should_exclude_content(content, user_message):
                continue

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
        results = self.memory_service.search(
            user_message,
            memory_type="reflection",
            limit=3,
        )

        if not results:
            results = self.memory_service.read(memory_type="reflection", limit=1)

        items: list[ContextItem] = []

        for result in results:
            content = result.get("text", "")

            if self._should_exclude_content(content, user_message):
                continue

            items.append(
                ContextItem(
                    id=result.get("id", ""),
                    content=content,
                    source="reflection",
                    item_type="reflection",
                    score=1.0,
                    timestamp=result.get("timestamp"),
                    tags=result.get("tags", []),
                    metadata=result,
                )
            )

        return items

    def get_conversation_items(self, user_message: str) -> list[ContextItem]:
        results = search_conversation_memories(user_message, top_k=6)
        items: list[ContextItem] = []

        for result in results:
            memory = result.get("memory", {})
            content = memory.get("text", "")

            if self._should_exclude_content(content, user_message):
                continue

            items.append(
                ContextItem(
                    id=memory.get("timestamp", ""),
                    content=content,
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
        memory_items = self._deduplicate_items(memory_items)

        return memory_items, reflection_items

    def _deduplicate_items(self, items: list[ContextItem]) -> list[ContextItem]:
        seen = set()
        deduped: list[ContextItem] = []

        for item in items:
            key = self._normalize_text(item.content)
            if not key:
                continue
            if key in seen:
                continue

            deduped.append(item)
            seen.add(key)

        return deduped

    def _should_exclude_content(self, content: str, user_message: str) -> bool:
        normalized_content = self._normalize_text(content)
        normalized_user_message = self._normalize_text(user_message)

        if not normalized_content:
            return True

        if len(normalized_content) < 40:
            return True

        meta_markers = (
            "user asked:",
            "ember responded:",
            "assistant responded:",
            "assistant said:",
            "### task:",
            "generate 1-3 broad tags",
            '"user_message":',
            '"memory_items":',
            '"reflection_items":',
            '"conversation_id":',
            '"chunk_id":',
        )

        if any(marker in normalized_content for marker in meta_markers):
            return True

        if normalized_content.startswith("{") or normalized_content.startswith("["):
            return True

        if "```" in content:
            return True

        if normalized_user_message and normalized_user_message in normalized_content:
            return True

        similarity = self._jaccard_similarity(
            self._tokenize(normalized_content),
            self._tokenize(normalized_user_message),
        )

        if similarity > 0.60:
            return True

        return False

    def _tokenize(self, text: str) -> set[str]:
        return set(re.findall(r"\b[a-z0-9]{3,}\b", text.lower()))

    def _jaccard_similarity(self, a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0

        union = a | b
        if not union:
            return 0.0

        return len(a & b) / len(union)

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())