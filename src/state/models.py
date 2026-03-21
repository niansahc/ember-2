"""
src/state/models.py

Typed data models for the Ember-2 state layer.

Two representations are defined:

  StateRecord  — the canonical vault artifact, written to disk as JSON.
                 Follows the base memory record schema (id, timestamp, type,
                 text, source, tags, metadata) so it is compatible with
                 MemoryService and the standard vault layout.

  StateItem    — the lightweight in-memory object passed to the context layer.
                 Produced by StateResolver when computing current state from
                 the vault. Contains only what the context builder needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Valid state categories
# ---------------------------------------------------------------------------

# These are the allowed values for StateRecord.type and StateItem.category.
# The taxonomy may grow, but all values must map to a single clear meaning.
VALID_STATE_CATEGORIES: frozenset[str] = frozenset(
    {
        "active_project",  # A project currently in progress
        "open_loop",       # Something unresolved that needs follow-up
        "current_focus",   # What is being actively worked on right now
        "blocker",         # Something preventing progress
        "routine",         # A recurring habit, check-in, or process
        "priority",        # A near-term priority item
        "next_action",     # A concrete next step to take
    }
)


# ---------------------------------------------------------------------------
# StateRecord — canonical vault artifact
# ---------------------------------------------------------------------------

@dataclass
class StateRecord:
    """
    A canonical state artifact written to the private vault as a JSON file.

    Follows the base memory record schema defined in CLAUDE.md so that
    StateRecord objects are structurally compatible with other vault records
    and can be read back by MemoryService if needed.

    Fields
    ------
    id : str
        Stable record identifier. Convention: ISO timestamp string,
        e.g. "2026-03-21T14-30-00".
    timestamp : str
        ISO 8601 creation time. Used for chronological ordering and
        "latest record wins" resolution logic in StateResolver.
    type : str
        State category. Must be one of VALID_STATE_CATEGORIES.
        Maps to the 'type' field in the base memory schema.
    text : str
        Human-readable description of this state artifact.
        This is the primary content shown in the context layer.
    source : str
        The subsystem or process that created this record,
        e.g. "user_input", "reflection_engine", "state_service".
    tags : list[str]
        Optional lightweight labels for filtering and search.
    metadata : dict[str, Any]
        Structured machine-readable context. Keep flat or shallow.
        Useful fields: "project", "due_date", "priority", "status".
    """

    id: str
    timestamp: str
    type: str
    text: str
    source: str
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Validate that the type field is a recognised state category."""
        if self.type not in VALID_STATE_CATEGORIES:
            raise ValueError(
                f"Invalid state type '{self.type}'. "
                f"Must be one of: {sorted(VALID_STATE_CATEGORIES)}"
            )


# ---------------------------------------------------------------------------
# StateItem — lightweight context-layer representation
# ---------------------------------------------------------------------------

@dataclass
class StateItem:
    """
    A lightweight representation of a state artifact used by the context layer.

    StateItems are produced by StateResolver after it reads and resolves the
    current state from vault records. They are passed into ContextPacket as
    state_items and formatted into the prompt by ContextFormatter.

    Fields
    ------
    category : str
        State category, e.g. "current_focus", "blocker".
        Mirrors StateRecord.type. Must be one of VALID_STATE_CATEGORIES.
    text : str
        Human-readable content describing this state artifact.
        This is what gets injected into the context prompt.
    timestamp : str
        ISO 8601 timestamp of the source StateRecord.
        Used by StateResolver to determine recency when multiple
        records exist for the same category.
    priority : str | None
        Optional priority signal, e.g. "high", "medium", "low".
        Sourced from StateRecord.metadata.get("priority") when present.
        Defaults to None if not specified.
    """

    category: str
    text: str
    timestamp: str
    priority: str | None = None
