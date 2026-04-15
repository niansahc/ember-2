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
    # Cluster 8 / task #24: authorship signal sourced from the SQLite
    # index column. One of: first_person, third_party, mixed, unknown.
    # Defaults to "unknown" — the ranker's authorship multiplier falls
    # back to a conservative 0.5x for unknown items on relational queries.
    authorship: str = "unknown"


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
    # Pre-computed query embedding for the user message. Populated once
    # during context assembly and reused by the lodestone resolver in the
    # prompt builder. Avoids a redundant embed_text() call (perf: 3→1
    # embedding calls per request).
    query_embedding: list[float] | None = None
    # Cluster 8 / task #24 zero-hit signal: True when the query was
    # classified as relational/identity AND the authorship multiplier
    # zeroed out every candidate item. Prompt builder renders an extra
    # authority-rules line instructing the model to acknowledge the gap
    # explicitly rather than synthesize from ingested content.
    relational_query_empty: bool = False

    def all_items(self) -> list[ContextItem]:
        # Order matches TDD context packet order:
        # state → reflections → source memories
        # Note: state_items are StateItem objects (not ContextItem), so they
        # are intentionally excluded here — this method returns only ContextItems.
        return self.reflection_items + self.memory_items
