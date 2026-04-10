"""
src/state/state_resolver.py

StateResolver computes "current state" from the vault records managed by
StateService.

Resolution rules (ADR-011):
  Single-record categories (current_focus, active_project, etc.):
    Most recent StateRecord by timestamp wins. All older records are history.
  Multi-record categories (open_loop, next_action):
    All non-deleted, non-resolved records returned, capped at 5 most recent.

This mirrors the append-only design principle from CLAUDE.md — records are
never deleted or overwritten. The resolver reads everything and surfaces only
the latest record per category as a StateItem for the context layer.

StateResolver does not write to the vault. That is StateService's job.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.state.models import (
    VALID_STATE_CATEGORIES,
    MULTI_RECORD_CATEGORIES,
    MAX_MULTI_RECORDS,
    StateItem,
    StateRecord,
)
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

        Skips records where metadata.resolved is True — a resolved
        single-record category record should not surface as the active
        value even if it is the newest. This was a known issue
        (CLAUDE.md Known Issues, StateResolver._latest_per_category).

        Timestamp comparison is lexicographic string sort. This works
        correctly because the timestamp format is "%Y-%m-%dT%H-%M-%S" —
        a fixed-width, zero-padded string that sorts chronologically.
        (Note: hyphens replace colons in the time portion for Windows
        filename safety — see state_service.py make_record() for details.)
        """
        latest: dict[str, StateRecord] = {}

        for record in records:
            # Skip resolved records — they should not win "latest" for
            # their category regardless of timestamp.
            if record.metadata and record.metadata.get("resolved"):
                continue

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

        Single-record categories (current_focus, active_project, etc.):
            One StateItem per category, latest record wins.

        Multi-record categories (open_loop, next_action — per ADR-011):
            All non-deleted records returned, capped at MAX_MULTI_RECORDS
            most recent per category.

        Timers (BUG-004):
            Resolved separately by timer_id, not by category. Only timers
            whose latest record has status="running" are surfaced. Each
            active timer becomes a StateItem with the elapsed time baked
            into its text field. See _resolve_active_timers().

        Items are ordered by category name (alphabetical) for deterministic
        output — callers that care about ordering should sort by priority
        or timestamp themselves.

        Returns
        -------
        list[StateItem]
            Current state items. Returns an empty list if the vault has
            no state records.
        """
        records = self._service.read_all()

        if not records:
            return self._resolve_active_timers()

        # Staleness cutoff — applies to both single and multi-record categories.
        # Records older than this are considered stale and excluded from the
        # current state. Exemptions: onboarding (system flag, permanent) and
        # timer (resolved separately by timer_id + status).
        from src.core.config import get_state_staleness_days
        staleness_days = get_state_staleness_days()
        staleness_cutoff = (datetime.now() - timedelta(days=staleness_days)).strftime(
            "%Y-%m-%dT%H-%M-%S"
        )

        # Categories exempt from staleness filtering.
        _STALENESS_EXEMPT = {"onboarding", "timer"}

        # Separate records by single vs multi-record categories.
        # Timer records have their own resolution semantics (latest-per-
        # timer_id with status filtering) and are excluded from both the
        # single-category and multi-category buckets here.
        single_records = [
            r for r in records
            if r.type not in MULTI_RECORD_CATEGORIES and r.type != "timer"
        ]
        multi_records = [r for r in records if r.type in MULTI_RECORD_CATEGORIES]

        items = []

        # Single-record: latest wins per category, then staleness filter.
        # _latest_per_category already skips resolved records.
        latest = self._latest_per_category(single_records)
        for category, record in sorted(latest.items()):
            # Apply staleness filter to non-exempt single-record categories.
            # A 12-day-old "routine" record is not anyone's current state.
            if (
                category not in _STALENESS_EXEMPT
                and record.timestamp
                and record.timestamp < staleness_cutoff
            ):
                continue
            items.append(self._record_to_item(record))

        multi_by_cat: dict[str, list[StateRecord]] = {}
        for record in multi_records:
            # Skip deleted records
            if record.metadata and record.metadata.get("deleted"):
                continue
            # Skip resolved records
            if record.metadata and record.metadata.get("resolved"):
                continue
            # Skip stale multi-record items (next_action, open_loop)
            if record.timestamp and record.timestamp < staleness_cutoff:
                continue
            multi_by_cat.setdefault(record.type, []).append(record)

        for category in sorted(multi_by_cat.keys()):
            cat_records = multi_by_cat[category]
            # Sort newest first, cap at MAX_MULTI_RECORDS
            cat_records.sort(key=lambda r: r.timestamp, reverse=True)
            for record in cat_records[:MAX_MULTI_RECORDS]:
                items.append(self._record_to_item(record))

        # Active timers (BUG-004) — separate resolution path because timer
        # state is grouped by timer_id and filtered by latest-record status,
        # not by latest-per-category.
        items.extend(self._resolve_active_timers())

        return items

    def _resolve_active_timers(self) -> list[StateItem]:
        """Convert active timers into StateItems with elapsed-time text.

        Calls timer_service.get_active_timers(), which already groups by
        timer_id and filters to only those whose latest record is running.
        Each surviving record is rendered as a StateItem whose text field
        carries a human-readable elapsed-time phrase computed from
        metadata.started_at at resolution time.
        """
        # Local import to avoid an import cycle: timer_service imports
        # state_service, and importing it at module top would force the
        # state package to load timer_service before state_resolver finishes
        # initializing in some test contexts.
        from src.state.timer_service import format_elapsed, get_active_timers

        items: list[StateItem] = []
        for record in get_active_timers(service=self._service):
            started_at = (record.metadata or {}).get("started_at", "")
            elapsed = format_elapsed(started_at)
            label = record.text or "(unnamed)"
            items.append(
                StateItem(
                    category="timer",
                    text=f"Timer '{label}' started {elapsed}",
                    timestamp=record.timestamp,
                    priority=None,
                )
            )
        return items

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
