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
    # Real state record ids are unique (make_record spins the timestamp to
    # guarantee it). Fold the distinct text into the fixture id so two records
    # of the same type/age do not collide — an id collision is an impossible
    # state that the resolved-id suppression (B-STATE-001) is not expected to
    # tolerate.
    return StateRecord(
        id=f"test-{state_type}-{days_ago}-{text}",
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


def test_stale_current_focus_filtered():
    """current_focus older than STATE_STALENESS_DAYS is excluded.

    Previously, single-record categories used latest-wins with no staleness
    filter. Updated to apply the same staleness threshold because a 30-day-old
    current_focus is not anyone's current state.
    """
    records = [
        _make_record("current_focus", "Old focus from 30 days ago", days_ago=30),
    ]
    service = FakeStateService(records)
    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    texts = [i.text for i in items]
    assert "Old focus from 30 days ago" not in texts


def test_fresh_current_focus_surfaces():
    """current_focus within STATE_STALENESS_DAYS still surfaces via latest-wins."""
    records = [
        _make_record("current_focus", "Fresh focus from today", days_ago=0),
    ]
    service = FakeStateService(records)
    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    texts = [i.text for i in items]
    assert "Fresh focus from today" in texts


def test_stale_active_project_filtered():
    """active_project older than STATE_STALENESS_DAYS is excluded."""
    records = [
        _make_record("active_project", "Project from 20 days ago", days_ago=20),
    ]
    service = FakeStateService(records)
    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    texts = [i.text for i in items]
    assert "Project from 20 days ago" not in texts


def test_fresh_active_project_surfaces():
    """active_project within STATE_STALENESS_DAYS still surfaces."""
    records = [
        _make_record("active_project", "Recent project from today", days_ago=0),
    ]
    service = FakeStateService(records)
    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    texts = [i.text for i in items]
    assert "Recent project from today" in texts


def test_onboarding_exempt_from_staleness():
    """onboarding is a system flag — exempt from staleness filtering."""
    records = [
        _make_record("onboarding", "onboarding_complete", days_ago=30),
    ]
    service = FakeStateService(records)
    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    texts = [i.text for i in items]
    assert "onboarding_complete" in texts


def test_resolved_single_record_excluded():
    """A resolved single-record category should not surface even if newest."""
    records = [
        _make_record("priority", "Resolved priority", days_ago=0, resolved=True),
    ]
    service = FakeStateService(records)
    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    texts = [i.text for i in items]
    assert "Resolved priority" not in texts


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
