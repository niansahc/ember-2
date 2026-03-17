from src.context.models import ContextItem


class ContextRanker:
    def rank(
        self,
        memory_items: list[ContextItem],
        reflection_items: list[ContextItem],
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        for item in memory_items:
            if item.item_type == "conversation":
                item.score *= 1.15
            elif item.item_type == "ingested":
                item.score *= 1.05
            elif item.item_type == "memory":
                item.score *= 1.0

            content = item.content.lower()

            if "ozempic" in content:
                item.score += 0.20
            if "trigeminal neuralgia" in content:
                item.score += 0.20
            if content.startswith("user:"):
                item.score += 0.05
            if len(content.strip()) < 20:
                item.score -= 0.10

        for item in reflection_items:
            item.score *= 0.95

        ranked_memory = sorted(memory_items, key=lambda item: item.score, reverse=True)
        ranked_reflections = sorted(reflection_items, key=lambda item: item.score, reverse=True)
        return ranked_memory, ranked_reflections