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

        query_terms = [t for t in user_message.lower().split() if t]

        def relevance_hits(item) -> int:
            content = item.content.lower()
            return sum(1 for term in query_terms if term in content)

        relevant_memory = [m for m in ranked_memory if relevance_hits(m) > 0]

        if not relevant_memory:
            relevant_memory = ranked_memory

        seen = set()
        deduped_memory = []

        for item in relevant_memory:
            key = item.content.strip().lower()
            if key not in seen:
                deduped_memory.append(item)
                seen.add(key)

        conversation_memories = [
            m for m in deduped_memory if m.item_type == "conversation"
        ]
        ingested_memories = [
            m for m in deduped_memory if m.item_type == "ingested"
        ]
        other_memories = [
            m for m in deduped_memory
            if m.item_type not in {"conversation", "ingested"}
        ]

        selected_memory: list = []

        if conversation_memories:
            selected_memory.extend(conversation_memories[:3])

        if ingested_memories:
            selected_memory.extend(ingested_memories[:3])

        if other_memories:
            selected_memory.extend(other_memories[:2])

        if not selected_memory:
            selected_memory = deduped_memory[:6]

        selected_reflections = ranked_reflections[:2]

        return self.formatter.format(
            user_message=user_message,
            memory_items=selected_memory,
            reflection_items=selected_reflections,
        )