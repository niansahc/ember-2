from src.context.models import ContextItem


class ContextRanker:
    def rank(
        self,
        memory_items: list[ContextItem],
        reflection_items: list[ContextItem],
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        """
        Rank and trim context items.
        Placeholder implementation for now: sort by score descending.
        """
        ranked_memory = sorted(memory_items, key=lambda item: item.score, reverse=True)
        ranked_reflections = sorted(
            reflection_items, key=lambda item: item.score, reverse=True
        )
        return ranked_memory, ranked_reflections
    