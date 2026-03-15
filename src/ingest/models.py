# src/ingest/models.py

from dataclasses import dataclass, field
from typing import Any


@dataclass
class NormalizedDocument:
    source: str
    doc_id: str
    title: str
    created_at: str | None
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChunkedDocument:
    source: str
    doc_id: str
    chunk_id: str
    title: str
    created_at: str | None
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)