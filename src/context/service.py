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
        memory_items, reflection_items = self.retriever.retrieve(user_message)
        ranked_memory, ranked_reflections = self.ranker.rank(
            memory_items, reflection_items
        )

        # deduplicate memory items by content
        seen = set()
        deduped_memory = []

        for item in ranked_memory:
            key = item.content.strip()
            if key not in seen:
                seen.add(key)
                deduped_memory.append(item)

        ranked_memory = deduped_memory
        return self.formatter.format(
            user_message=user_message,
            memory_items=ranked_memory,
            reflection_items=ranked_reflections,
        )