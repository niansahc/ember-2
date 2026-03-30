"""
src/tasks/models.py

Typed data models for the Ember-2 task layer (TDD section 17).

Two representations:

  TaskRecord  -- the canonical vault artifact, written to disk as JSON.
                 Follows the base memory record schema so it is compatible
                 with MemoryService and the standard vault layout.

  TaskItem    -- lightweight in-memory object for the context layer.
                 Produced by TaskResolver when computing active tasks.
                 Contains only what the prompt builder needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Valid task statuses
# ---------------------------------------------------------------------------

VALID_TASK_STATUSES: frozenset[str] = frozenset(
    {
        "proposed",   # Suggested but not yet accepted
        "active",     # Accepted and in progress
        "done",       # Completed
        "cancelled",  # Abandoned
    }
)


# ---------------------------------------------------------------------------
# TaskRecord -- canonical vault artifact
# ---------------------------------------------------------------------------

@dataclass
class TaskRecord:
    """
    A canonical task artifact written to the private vault as a JSON file.

    Fields
    ------
    id : str
        Stable record identifier. Convention: ISO timestamp string,
        e.g. "2026-03-30T14-30-00-123456".
    timestamp : str
        ISO creation time. Used for chronological ordering.
    type : str
        Always "task". Matches the memory record schema type field.
    title : str
        Short human-readable task name.
    status : str
        Lifecycle state. Must be one of VALID_TASK_STATUSES.
    text : str
        Human-readable description. May be the same as title or richer.
    source : str
        The subsystem that created this record, e.g. "user_input",
        "task_detector", "commitment_detector".
    project_id : str | None
        If this task belongs to a project, its ID. None for general tasks.
    tags : list[str]
        Optional labels for filtering and search.
    metadata : dict[str, Any]
        Structured machine-readable context. Useful fields: session_id,
        created_from, priority, due_date.
    """

    id: str
    timestamp: str
    type: str
    title: str
    status: str
    text: str
    source: str
    project_id: str | None = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate type and status fields."""
        if self.type != "task":
            raise ValueError(
                f"TaskRecord type must be 'task', got '{self.type}'"
            )
        if self.status not in VALID_TASK_STATUSES:
            raise ValueError(
                f"Invalid task status '{self.status}'. "
                f"Must be one of: {sorted(VALID_TASK_STATUSES)}"
            )


# ---------------------------------------------------------------------------
# TaskItem -- lightweight context-layer representation
# ---------------------------------------------------------------------------

@dataclass
class TaskItem:
    """
    A lightweight task representation for the context layer.

    Produced by TaskResolver. Passed into ContextPacket and formatted
    into the prompt by PromptBuilder.

    Fields
    ------
    id : str
        Task record ID.
    title : str
        Short task name shown in the prompt.
    status : str
        Current lifecycle state (proposed or active for context injection).
    project_id : str | None
        Project scope, if any.
    priority : str | None
        Optional priority signal from metadata.
    """

    id: str
    title: str
    status: str
    project_id: str | None = None
    priority: str | None = None
