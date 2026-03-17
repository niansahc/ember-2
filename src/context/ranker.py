from __future__ import annotations

import re
from datetime import datetime, timezone

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
        metadata = getattr(item, "metadata", {}) or {}
        content = item.content.lower().strip()

        if item_type == "conversation":
            score += 0.10
        elif item_type == "reflection":
            score += 0.06
        elif item_type == "memory":
            score += 0.04
        elif item_type == "ingested":
            score += 0.00

        role = metadata.get("role")
        content_kind = metadata.get("content_kind")

        if role == "user":
            score += 0.12
        elif role == "assistant":
            score -= 0.08
        elif role in {"tool", "system"}:
            score -= 0.20

        if content_kind == "experience":
            score += 0.14
        elif content_kind == "user_content":
            score += 0.05
        elif content_kind == "answer":
            score += 0.03
        elif content_kind == "question":
            score -= 0.10

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

        if self._looks_like_low_value_prompt(content):
            score -= 0.18

        score += self._recency_boost(item.timestamp)

        item.score = score
        return item

    def _score_reflection_item(self, item: ContextItem) -> ContextItem:
        score = float(item.score)
        content = item.content.lower().strip()

        score *= 0.95

        if len(content) < 30:
            score -= 0.08

        score += self._recency_boost(item.timestamp) * 0.5

        item.score = score
        return item

    def _recency_boost(self, timestamp: str | None) -> float:
        if not timestamp:
            return 0.0

        try:
            ts = float(timestamp)
            item_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (TypeError, ValueError):
            try:
                normalized = timestamp.replace("Z", "+00:00")
                item_dt = datetime.fromisoformat(normalized)
                if item_dt.tzinfo is None:
                    item_dt = item_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                return 0.0

        now = datetime.now(timezone.utc)
        age_days = max((now - item_dt).days, 0)

        if age_days <= 7:
            return 0.18
        if age_days <= 30:
            return 0.12
        if age_days <= 90:
            return 0.06
        if age_days <= 365:
            return 0.02
        return -0.03

    def _looks_like_low_value_prompt(self, content: str) -> bool:
        markers = (
            "what have i been working on today?",
            "yes, tell me all the things you see",
            "do you think i am doing okay or struggling?",
        )
        return any(marker in content for marker in markers)

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"\b[a-z0-9]{3,}\b", text)