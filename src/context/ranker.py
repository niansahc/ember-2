from __future__ import annotations

import re

from src.context.models import ContextItem


class ContextRanker:
    def rank(
        self,
        memory_items: list[ContextItem],
        reflection_items: list[ContextItem],
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        ranked_memory = [self._score_memory_item(item) for item in memory_items]
        ranked_reflections = [self._score_reflection_item(item) for item in reflection_items]

        ranked_memory.sort(key=lambda item: item.score, reverse=True)
        ranked_reflections.sort(key=lambda item: item.score, reverse=True)

        return ranked_memory, ranked_reflections

    def _score_memory_item(self, item: ContextItem) -> ContextItem:
        score = float(item.score)

        item_type = getattr(item, "item_type", "")

        if item_type == "conversation":
            score *= 1.10
        elif item_type == "ingested":
            score *= 1.05
        elif item_type == "memory":
            score *= 1.00
        else:
            score *= 0.98

        content = item.content.lower().strip()

        if content.startswith("user:"):
            score += 0.04

        if len(content) < 20:
            score -= 0.10
        elif len(content) < 50:
            score -= 0.04
        elif len(content) > 1200:
            score -= 0.03

        token_count = len(self._tokenize(content))
        if token_count < 5:
            score -= 0.05

        item.score = score
        return item

    def _score_reflection_item(self, item: ContextItem) -> ContextItem:
        score = float(item.score)

        content = item.content.lower().strip()

        score *= 0.95

        if len(content) < 30:
            score -= 0.08

        item.score = score
        return item

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"\b[a-z0-9]{3,}\b", text)