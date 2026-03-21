"""
src/state/state_resolver.py

StateResolver computes "current state" from the vault records managed by
StateService.

The resolution rule is simple and explicit:
  For each state category, the most recent StateRecord (by timestamp) is
  the current one. All older records for that category are history.

This mirrors the append-only design principle from CLAUDE.md — records are
never deleted or overwritten. The resolver reads everything and surfaces only
the latest record per category as a StateItem for the context layer.

StateResolver does not write to the vault. That is StateService's job.
"""

from __future__ import annotations

from src.state.models import VALID_STATE_CATEGORIES, StateItem, StateRecord
from src.state.state_service import StateService


class StateResolver:
    """
    Resolves current state from vault records.

    Reads all StateRecord objects via StateService and applies "latest record
    wins" per category, returning lightweight StateItem objects suitable for
    use in ContextPacket.

    Usage
    -----
    resolver = StateResolver()

    # All current state items (one per category that has records)
    items = resolver.get_current_state()

    # Current state for one category
    focus = resolver.get_current_by_category("current_focus")

    # Dict keyed by category, useful for structured context assembly
    state_dict = resolver.get_current_as_dict()
    """

    def __init__(self, service: StateService | None = None) -> None:
        """
        Parameters
        ----------
        service : StateService | None
            The StateService to use for reading vault records. If None, a
            default StateService is created (reads from PRIVATE_VAULT_PATH).
            Pass an explicit instance in tests to control the vault path.
        """
        self._service = service or StateService()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_to_item(self, record: StateRecord) -> StateItem:
        """
        Convert a StateRecord into a StateItem.

        Extracts priority from record.metadata if present; otherwise
        leaves it as None.
        """
        priority = record.metadata.get("priority") if record.metadata else None

        # Ensure priority is a string if present — metadata values could be
        # anything, so we normalise defensively.
        if priority is not None:
            priority = str(priority)

        return StateItem(
            category=record.type,
            text=record.text,
            timestamp=record.timestamp,
            priority=priority,
        )

    def _latest_per_category(
        self, records: list[StateRecord]
    ) -> dict[str, StateRecord]:
        """
        Given a flat list of StateRecords, return a dict mapping each
        category to its single most-recent record.

        Timestamp comparison is lexicographic string sort. This works
        correctly because the timestamp format is "%Y-%m-%dT%H-%M-%S" —
        a fixed-width, zero-padded string that sorts chronologically.
        (Note: hyphens replace colons in the time portion for Windows
        filename safety — see state_service.py make_record() for details.)
        """
        latest: dict[str, StateRecord] = {}

        for record in records:
            category = record.type
            existing = latest.get(category)

            # Replace if this record is newer (string comparison is safe here
            # given the fixed-width timestamp format).
            if existing is None or record.timestamp > existing.timestamp:
                latest[category] = record

        return latest

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_current_state(self) -> list[StateItem]:
        """
        Return the current state as a list of StateItems.

        One StateItem is returned per category that has at least one record
        in the vault. Categories with no records are omitted.

        Items are ordered by category name (alphabetical) for deterministic
        output — callers that care about ordering should sort by priority
        or timestamp themselves.

        Returns
        -------
        list[StateItem]
            Current state items, one per populated category.
            Returns an empty list if the vault has no state records.
        """
        records = self._service.read_all()

        if not records:
            return []

        latest = self._latest_per_category(records)

        # Sort by category name for stable, predictable output.
        return [
            self._record_to_item(record)
            for category, record in sorted(latest.items())
        ]

    def get_current_by_category(self, category: str) -> StateItem | None:
        """
        Return the current StateItem for a single category.

        Parameters
        ----------
        category : str
            A valid state category, e.g. "current_focus", "blocker".
            Must be one of VALID_STATE_CATEGORIES.

        Returns
        -------
        StateItem | None
            The most recent StateItem for that category, or None if no
            records exist for it in the vault.

        Raises
        ------
        ValueError
            If the category is not in VALID_STATE_CATEGORIES.
        """
        if category not in VALID_STATE_CATEGORIES:
            raise ValueError(
                f"Unknown state category '{category}'. "
                f"Must be one of: {sorted(VALID_STATE_CATEGORIES)}"
            )

        records = self._service.read_by_category(category)

        if not records:
            return None

        # read_by_category returns newest-first (from read_all), so index 0
        # is already the most recent record.
        return self._record_to_item(records[0])

    def get_current_as_dict(self) -> dict[str, StateItem]:
        """
        Return the current state as a dict keyed by category name.

        Useful for context assembly when the caller needs to look up a
        specific category without iterating the full list.

        Returns
        -------
        dict[str, StateItem]
            Mapping of category → current StateItem for all populated
            categories. Returns an empty dict if the vault has no state
            records.
        """
        items = self.get_current_state()
        return {item.category: item for item in items}
