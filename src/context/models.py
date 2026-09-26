from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from src.state.models import StateItem
from src.tasks.models import TaskItem

if TYPE_CHECKING:
    from src.safety.models import PatternSignal


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
    # authorship signal sourced from the SQLite
    # index column. One of: first_person, mixed, unknown.
    # third_party retired in #218 (no population, no live assigner).
    # Defaults to "unknown" — the ranker's authorship multiplier falls
    # back to a conservative 0.5x for unknown items on relational queries.
    authorship: str = "unknown"
    # ADR-015 amendment, implementation step 4: the record's actual
    # primary key in whichever SQLite store it came from (memory.db or
    # ingested.db) -- distinct from `id`, which is a different identifier
    # for a different job (path/chunk_id, used for session-scoped hedge
    # tracking in conversation_buffer.was_hedged). Deliberately a separate
    # field rather than overloading `id`: service._update_retrieval_stats
    # needs the real vectors.id to write retrieval stats back, and `id`
    # was never that value for most memory types (a file path, not the
    # row's primary key), which is why retrieval stats never updated.
    store_id: str | None = None


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
    # Zero-hit signal: True when the query was
    # classified as relational/identity AND the authorship multiplier
    # zeroed out every candidate item. Prompt builder renders an extra
    # authority-rules line instructing the model to acknowledge the gap
    # explicitly rather than synthesize from ingested content.
    relational_query_empty: bool = False
    # ADR-021 cross-session pattern signal. Populated post-retrieval by
    # detect_t2_pattern; None when no pattern was detected this turn.
    t2_pattern_signal: PatternSignal | None = None

    # --- delivery accounting (issue #227) --------------------------------
    #
    # The packet is a candidate set; the prompt renders a slice of it. ADR-015
    # heat is supposed to record delivery, so it has to be written against the
    # slice, not the packet. A single write sets last_retrieved_at to now,
    # which forces recency to 1.0 and heat to at least 0.625 -- over the 0.5
    # hot threshold outright. Under the old timing one appearance in a
    # candidate set was a guaranteed promotion to hot for a record the model
    # never saw.
    #
    # So the write is deferred. build_context arms the packet with a recorder,
    # the prompt builder reports what it actually rendered, and the adapter
    # commits once the prompt is final. Nothing renders, nothing is recorded --
    # which is the correct answer for a packet that never reached a model.
    delivered_items: list[ContextItem] = field(default_factory=list, repr=False)
    _delivery_recorder: Any | None = field(default=None, repr=False, compare=False)

    def arm_delivery_recorder(self, recorder) -> None:
        """Install the callback that commits retrieval stats for this turn.

        Left unarmed for read-only builds, so the read_only contract is
        expressed by never installing a writer rather than by a branch at
        write time.
        """
        self._delivery_recorder = recorder

    def begin_render(self) -> None:
        """Start a render pass, discarding any previous one.

        Discarding rather than accumulating is what makes the cascade-trim
        path correct: prompt_guardrail builds the same packet up to seven
        times, dropping sections each round, and only the last build is what
        the model receives.
        """
        self.delivered_items = []

    def record_rendered(self, items) -> None:
        """Record items this render pass put in front of the model."""
        self.delivered_items.extend(items)

    def commit_delivery(self) -> int:
        """Commit the recorded render. Idempotent.

        Disarms afterwards, so a second prompt build for the same turn --
        the constitutional review path rebuilds one -- cannot double count.
        Returns the number of records committed.
        """
        recorder = self._delivery_recorder
        if recorder is None:
            return 0
        self._delivery_recorder = None
        items = list(self.delivered_items)
        if items:
            recorder(items)
        return len(items)

    def all_items(self) -> list[ContextItem]:
        # Order matches TDD context packet order:
        # state → reflections → source memories
        # Note: state_items are StateItem objects (not ContextItem), so they
        # are intentionally excluded here — this method returns only ContextItems.
        return self.reflection_items + self.memory_items
