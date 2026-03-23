from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("ember.policies")


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
    # Signals to the ranker how much to boost state items for this query intent.
    # 0.0 = no special boost (default). Higher values prioritise current state
    # over memory and reflections — used for status/state queries.
    state_boost: float = 0.0
    # When True, ContextService will call web_search() and inject results into
    # the ContextPacket before prompt assembly.
    use_web_search: bool = False


def classify_query(user_message: str) -> ContextPolicy:
    q = user_message.lower()
    # Normalize curly apostrophes to straight so markers like "what's" match
    # regardless of input source (Open WebUI, mobile keyboards, etc.)
    q = q.replace("\u2018", "'").replace("\u2019", "'")
    logger.warning("[CLASSIFY] normalized query: %s", q[:120])

    state_markers = (
        "what am i working on",
        "what are my open loops",
        "what is my current focus",
        "what's my current focus",
        "current focus",
        "open loops",
        "what are my priorities",
        "active projects",
        "what are my blockers",
        "my blockers",
        "what is blocking",
        "what's blocking",
        "current state",
        "where am i at",
        "what am i focused on",
        "catch me up",
        "remind me what",
    )

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

    # Checked before factual_recall — "search the web" is more specific than
    # the bare "search" marker in factual_recall_markers.
    web_search_markers = (
        "search the web",
        "search online",
        "look up online",
        "look this up",
        "look it up",
        "google",
        "what's the latest",
        "what is the latest",
        "current news",
        "news about",
        "find online",
        "find this online",
        "find it online",
        "can you find",
        "web search",
    )

    # Status/state queries resolve against current state first — checked before
    # reflective so "what am I working on" routes to state, not reflection.
    if any(marker in q for marker in state_markers):
        return ContextPolicy(
            name="status_state",
            memory_weight=0.6,
            reflection_weight=0.5,
            recency_bias=0.8,
            diversity=False,
            prefer_active_work=True,
            state_boost=2.0,
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

    if any(marker in q for marker in web_search_markers):
        logger.warning("[CLASSIFY] intent=web_search")
        return ContextPolicy(
            name="web_search",
            memory_weight=0.5,
            reflection_weight=0.3,
            recency_bias=0.0,
            diversity=False,
            use_web_search=True,
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
