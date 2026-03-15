from src.context.models import ContextItem
from src.memory.service import MemoryService


class ContextRetriever:
    def __init__(self, memory_service: MemoryService | None = None):
        self.memory_service = memory_service or MemoryService()

    def get_memory_items(self, user_message: str) -> list[ContextItem]:
        return []

    def get_reflection_items(self, user_message: str) -> list[ContextItem]:
        return []

    def retrieve(self, user_message: str) -> tuple[list[ContextItem], list[ContextItem]]:
        memory_items = self.get_memory_items(user_message)
        reflection_items = self.get_reflection_items(user_message)
        return memory_items, reflection_items