from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ContextPolicy:
    name: str
    memory_weight: float = 1.0
    reflection_weight: float = 1.0
    recency_bias: float = 0.0
    diversity: bool = False
    prefer_experiences: bool = False
    prefer_active_work: bool = False
    prefer_exact_matches: bool = False


def classify_query(user_message: str) -> ContextPolicy:
    q = user_message.lower()

    reflective_markers = (
        "pattern",
        "patterns",
        "theme",
        "themes",
        "trend",
        "trends",
        "what do you notice",
        "what have you noticed",
        "reflect",
        "reflection",
        "summarize me",
        "summarise me",
    )

    recent_markers = (
        "recently",
        "lately",
        "today",
        "this week",
        "these days",
        "currently",
        "right now",
    )

    activity_markers = (
        "working on",
        "building",
        "doing",
        "focused on",
        "making progress on",
        "up to",
    )

    factual_recall_markers = (
        "when did",
        "what did i say",
        "where did i mention",
        "find",
        "look up",
        "search",
        "recall",
        "remember when",
    )

    if any(marker in q for marker in reflective_markers):
        return ContextPolicy(
            name="reflective",
            memory_weight=0.7,
            reflection_weight=1.4,
            recency_bias=0.2,
            diversity=True,
            prefer_experiences=True,
        )

    if any(marker in q for marker in factual_recall_markers):
        return ContextPolicy(
            name="factual_recall",
            memory_weight=1.2,
            reflection_weight=0.4,
            recency_bias=0.3,
            diversity=False,
            prefer_exact_matches=True,
        )

    if any(marker in q for marker in recent_markers) and any(
        marker in q for marker in activity_markers
    ):
        return ContextPolicy(
            name="recent_activity",
            memory_weight=1.3,
            reflection_weight=0.7,
            recency_bias=1.2,
            diversity=True,
            prefer_active_work=True,
        )

    if any(marker in q for marker in recent_markers):
        return ContextPolicy(
            name="recent",
            memory_weight=1.1,
            reflection_weight=0.9,
            recency_bias=1.0,
            diversity=True,
        )

    if any(marker in q for marker in activity_markers):
        return ContextPolicy(
            name="activity",
            memory_weight=1.2,
            reflection_weight=0.6,
            recency_bias=0.5,
            diversity=True,
            prefer_active_work=True,
        )

    return ContextPolicy(name="default")