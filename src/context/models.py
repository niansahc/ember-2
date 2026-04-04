from dataclasses import dataclass, field
from typing import Any

from src.state.models import StateItem
from src.tasks.models import TaskItem


@dataclass
class ContextItem:
    id: str
    content: str
    source: str
    item_type: str
    score: float = 0.0
    memory_type: str | None = None
    timestamp: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    # ADR-015: Memory tier (hot/warm/cold). Defaults to "hot" for
    # backward compatibility with items that predate tiering.
    tier: str = "hot"


@dataclass
class ContextPacket:
    user_message: str
    memory_items: list[ContextItem] = field(default_factory=list)
    reflection_items: list[ContextItem] = field(default_factory=list)
    # Current operational state (active projects, focus, blockers, open loops,
    # etc.) resolved by StateResolver. Injected into the prompt before
    # reflections and memory, per TDD context order:
    # state → reflections → source memories → reference → user query.
    state_items: list[StateItem] = field(default_factory=list)
    # Active tasks (proposed + active) resolved by TaskResolver.
    # Injected into prompt after state, before reflections.
    task_items: list[TaskItem] = field(default_factory=list)
    web_items: list[dict] = field(default_factory=list)
    # Raw base64 image strings (data URL prefix stripped) for vision requests.
    # Populated by openai_adapter when the user uploads an image.
    image_data: list[str] = field(default_factory=list)
    summary: str | None = None

    def all_items(self) -> list[ContextItem]:
        # Order matches TDD context packet order:
        # state → reflections → source memories
        # Note: state_items are StateItem objects (not ContextItem), so they
        # are intentionally excluded here — this method returns only ContextItems.
        return self.reflection_items + self.memory_items
