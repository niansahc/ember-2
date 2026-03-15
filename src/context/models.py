from dataclasses import dataclass, field
from typing import Any


@dataclass
class ContextItem:
    id: str
    content: str
    source: str
    item_type: str
    score: float = 0.0
    timestamp: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContextPacket:
    user_message: str
    memory_items: list[ContextItem] = field(default_factory=list)
    reflection_items: list[ContextItem] = field(default_factory=list)
    summary: str | None = None

    def all_items(self) -> list[ContextItem]:
        return self.memory_items + self.reflection_items