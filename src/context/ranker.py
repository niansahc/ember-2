from __future__ import annotations

import re
from datetime import datetime, timezone

from src.context.models import ContextItem
from src.state.models import StateItem


class ContextRanker:
    def apply_policy(self, items: list[ContextItem], policy) -> list[ContextItem]:
        adjusted: list[ContextItem] = []

        for item in items:
            score = float(item.score)

            if item.item_type == "reflection":
                score *= policy.reflection_weight
            else:
                score *= policy.memory_weight

            if getattr(policy, "recency_bias", 0.0):
                score += self._recency_boost(item.timestamp) * float(policy.recency_bias)

            content = item.content.lower()
            metadata = getattr(item, "metadata", {}) or {}
            content_kind = metadata.get("content_kind")

            if getattr(policy, "prefer_experiences", False):
                if content_kind == "experience" or self._looks_like_experience(content):
                    score += 0.20

            if getattr(policy, "prefer_active_work", False):
                if self._looks_like_active_work(content, metadata):
                    score += 0.22

            if getattr(policy, "prefer_exact_matches", False):
                queryish_bonus = 0.0
                if content_kind == "question":
                    queryish_bonus -= 0.05
                else:
                    queryish_bonus += 0.03
                score += queryish_bonus

            item.score = score
            adjusted.append(item)

        return adjusted

    def apply_state_boost(
        self,
        state_items: list[StateItem],
        policy,
    ) -> list[StateItem]:
        """
        Apply policy state_boost to state items.

        For status_state queries (state_boost > 0), state items are
        already the primary source of truth — this method adds a score
        attribute to StateItem objects so they can be prioritized in
        context assembly.

        StateItem has no score field by default — we attach one via
        a simple wrapper approach: return items sorted by priority
        (high > medium > low > None) when state_boost > 0,
        otherwise return as-is.
        """
        boost = getattr(policy, "state_boost", 0.0)

        if not state_items or boost == 0.0:
            return state_items

        priority_order = {"high": 3, "medium": 2, "low": 1}

        return sorted(
            state_items,
            key=lambda item: priority_order.get(item.priority or "", 0),
            reverse=True,
        )

    def apply_project_boost(
        self,
        items: list[ContextItem],
        project_id: str | None,
    ) -> list[ContextItem]:
        """
        Boost memories that belong to the active project (ADR-007).

        This is a boost, not a filter — all items are returned, but items
        whose metadata.project_id matches the active project get a score
        increase of 0.15. This is meaningful enough to promote project-relevant
        memories without overwhelming general recall.

        If project_id is None (no active project), items are returned unchanged.
        """
        if not project_id or not items:
            return items

        for item in items:
            metadata = getattr(item, "metadata", {}) or {}
            if metadata.get("project_id") == project_id:
                item.score = float(item.score) + 0.15

        return items

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

    def _looks_like_experience(self, content: str) -> bool:
        markers = (
            "i am",
            "i'm",
            "i was",
            "i have",
            "i've",
            "i feel",
            "i felt",
            "today",
            "yesterday",
            "this week",
            "lately",
            "noticed",
            "experiencing",
            "having",
            "trying",
        )
        return any(marker in content for marker in markers)

    def _looks_like_active_work(self, content: str, metadata: dict) -> bool:
        title = str(metadata.get("title", "")).lower()

        markers = (
            "working on",
            "trying to",
            "focused on",
            "making progress",
            "next step",
            "next steps",
            "plan",
            "planning",
            "started",
            "finished",
            "need to",
            "figuring out",
            "stuck",
            "blocked",
            "updating",
            "changing",
            "organizing",
            "building",
            "improving",
            "fixing",
        )

        return any(marker in content for marker in markers) or any(
            marker in title for marker in markers
        )

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"\b[a-z0-9]{3,}\b", text)
