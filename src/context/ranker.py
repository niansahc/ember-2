from src.context.models import ContextItem


class ContextRanker:
    def rank(
        self,
        memory_items: list[ContextItem],
        reflection_items: list[ContextItem],
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        """
        Rank and trim context items.
        """

        # normalize memory scores
        for item in memory_items:
            if item.item_type == "conversation":
                item.score *= 0.9
            elif item.item_type == "memory":
                item.score *= 1.0

        # reflections slightly boosted but not dominant
        for item in reflection_items:
            item.score *= 0.95

        ranked_memory = sorted(memory_items, key=lambda item: item.score, reverse=True)
        ranked_reflections = sorted(
            reflection_items, key=lambda item: item.score, reverse=True
        )

        return ranked_memory, ranked_reflections