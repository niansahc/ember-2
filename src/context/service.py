from src.context.formatter import ContextFormatter
from src.context.models import ContextPacket
from src.context.ranker import ContextRanker
from src.context.retriever import ContextRetriever


class ContextService:
    def __init__(
        self,
        retriever: ContextRetriever | None = None,
        ranker: ContextRanker | None = None,
        formatter: ContextFormatter | None = None,
    ) -> None:
        self.retriever = retriever or ContextRetriever()
        self.ranker = ranker or ContextRanker()
        self.formatter = formatter or ContextFormatter()

    def build_context(self, user_message: str) -> ContextPacket:
        memory_items, reflection_items = self.retriever.retrieve(user_message, limit=3)

        ranked_memory, ranked_reflections = self.ranker.rank(
            memory_items, reflection_items
        )

        # simple deduplication by normalized text
        seen = set()
        deduped_memory = []

        for item in ranked_memory:
            key = item.metadata.get("normalized_text", item.content.lower().strip())

            if key not in seen:
                deduped_memory.append(item)
                seen.add(key)

        conversation_memories = [m for m in deduped_memory if m.item_type == "conversation"]
        journal_memories = [m for m in deduped_memory if m.item_type != "conversation"]

        selected_memory = conversation_memories[:1]
        selected_reflections = []

        return self.formatter.format(
            user_message=user_message,
        memory_items=selected_memory,
        reflection_items=selected_reflections,
        )
    