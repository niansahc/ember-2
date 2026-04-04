"""
Tests for StateResolver staleness filtering and state extraction suppression.
"""

from datetime import datetime, timedelta

from src.state.models import StateRecord, StateItem
from src.state.state_resolver import StateResolver


def _make_record(state_type: str, text: str, days_ago: int = 0, resolved: bool = False) -> StateRecord:
    ts = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H-%M-%S")
    metadata = {"state_type": state_type}
    if resolved:
        metadata["resolved"] = True
    return StateRecord(
        id=f"test-{state_type}-{days_ago}",
        timestamp=ts,
        type=state_type,
        text=text,
        source="test",
        tags=[],
        metadata=metadata,
    )


class FakeStateService:
    def __init__(self, records: list[StateRecord]):
        self._records = records

    def read_all(self) -> list[StateRecord]:
        return self._records


def test_stale_next_action_excluded():
    """next_action older than STATE_STALENESS_DAYS is excluded."""
    records = [
        _make_record("next_action", "Old task from last week", days_ago=10),
        _make_record("next_action", "Fresh task from today", days_ago=0),
    ]
    service = FakeStateService(records)
    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    texts = [i.text for i in items]
    assert "Fresh task from today" in texts
    assert "Old task from last week" not in texts


def test_stale_open_loop_excluded():
    """open_loop older than STATE_STALENESS_DAYS is excluded."""
    records = [
        _make_record("open_loop", "Old loop from 10 days ago", days_ago=10),
        _make_record("open_loop", "Recent loop from today", days_ago=0),
    ]
    service = FakeStateService(records)
    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    texts = [i.text for i in items]
    assert "Recent loop from today" in texts
    assert "Old loop from 10 days ago" not in texts


def test_current_focus_not_filtered_by_staleness():
    """current_focus is a single-record category -- latest wins regardless of age."""
    records = [
        _make_record("current_focus", "Old focus from 30 days ago", days_ago=30),
    ]
    service = FakeStateService(records)
    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    texts = [i.text for i in items]
    assert "Old focus from 30 days ago" in texts


def test_active_project_not_filtered_by_staleness():
    """active_project is a single-record category -- latest wins regardless of age."""
    records = [
        _make_record("active_project", "Project from 20 days ago", days_ago=20),
    ]
    service = FakeStateService(records)
    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    texts = [i.text for i in items]
    assert "Project from 20 days ago" in texts


def test_staleness_threshold_configurable():
    """STATE_STALENESS_DAYS default is 7."""
    from src.core.config import get_state_staleness_days
    assert get_state_staleness_days() == 7


def test_resolved_records_still_excluded():
    """Resolved records are excluded regardless of age."""
    records = [
        _make_record("next_action", "Resolved recent task", days_ago=0, resolved=True),
        _make_record("next_action", "Active recent task", days_ago=0),
    ]
    service = FakeStateService(records)
    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    texts = [i.text for i in items]
    assert "Active recent task" in texts
    assert "Resolved recent task" not in texts
