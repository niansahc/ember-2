"""
tests/test_state_layer.py

Tests for the Ember-2 state layer:
  - StateRecord and StateItem model validation
  - StateService vault read/write behaviour
  - StateResolver current-state resolution logic

All tests use pytest's tmp_path fixture as the vault root.
The real private vault is never touched.
"""

import json
import warnings
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.state.models import StateItem, StateRecord
from src.state.state_resolver import StateResolver


def _recent_ts(hours_ago: int = 0) -> str:
    """Generate a recent timestamp string in the state layer's hyphenated
    format, guaranteed to be within the staleness window."""
    dt = datetime.now() - timedelta(hours=hours_ago)
    return dt.strftime("%Y-%m-%dT%H-%M-%S")
from src.state.state_service import StateService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_service(tmp_path: Path) -> StateService:
    """Return a StateService backed by a temp directory."""
    return StateService(vault_path=tmp_path)


def make_resolver(tmp_path: Path) -> StateResolver:
    """Return a StateResolver backed by a StateService using a temp directory."""
    return StateResolver(service=make_service(tmp_path))


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------

def test_state_record_valid_type() -> None:
    """StateRecord with a valid type should construct without error."""
    record = StateRecord(
        id=_recent_ts(2),
        timestamp=_recent_ts(2),
        type="current_focus",
        text="Working on the state layer for Ember-2.",
        source="user_input",
    )

    assert record.type == "current_focus"
    assert record.text == "Working on the state layer for Ember-2."


def test_state_record_invalid_type() -> None:
    """StateRecord with an unrecognised type should raise ValueError."""
    with pytest.raises(ValueError, match="Invalid state type"):
        StateRecord(
            id=_recent_ts(2),
            timestamp=_recent_ts(2),
            type="not_a_real_type",
            text="This should fail.",
            source="test",
        )


# ---------------------------------------------------------------------------
# StateService tests
# ---------------------------------------------------------------------------

def test_state_service_write_and_read(tmp_path: Path) -> None:
    """Writing a StateRecord and calling read_all() should return it with matching fields."""
    service = make_service(tmp_path)

    record = StateRecord(
        id=_recent_ts(2),
        timestamp=_recent_ts(2),
        type="current_focus",
        text="Building the state layer.",
        source="test",
        tags=["ember2", "state"],
        metadata={"priority": "high"},
    )

    service.write(record)
    results = service.read_all()

    assert len(results) == 1
    result = results[0]
    assert result.type == "current_focus"
    assert result.text == "Building the state layer."
    assert result.source == "test"
    assert result.tags == ["ember2", "state"]
    assert result.metadata == {"priority": "high"}


def test_state_service_append_only(tmp_path: Path) -> None:
    """
    Writing the same record twice should not overwrite the original file.
    A UserWarning should be issued on the second write attempt.
    """
    service = make_service(tmp_path)

    record = StateRecord(
        id=_recent_ts(2),
        timestamp=_recent_ts(2),
        type="blocker",
        text="Original blocker text.",
        source="test",
    )

    # First write — should succeed silently.
    file_path = service.write(record)
    original_content = file_path.read_text(encoding="utf-8")

    # Second write — same filename, should warn and skip.
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        service.write(record)

    assert any("already exists" in str(w.message) for w in caught), (
        "Expected a warning about the file already existing"
    )

    # File content must be unchanged.
    assert file_path.read_text(encoding="utf-8") == original_content


def test_state_service_read_by_category(tmp_path: Path) -> None:
    """read_by_category() should return only records matching the requested type."""
    service = make_service(tmp_path)

    focus_record = StateRecord(
        id=_recent_ts(2),
        timestamp=_recent_ts(2),
        type="current_focus",
        text="Focus item.",
        source="test",
    )
    blocker_record = StateRecord(
        id=_recent_ts(1),
        timestamp=_recent_ts(1),
        type="blocker",
        text="Blocker item.",
        source="test",
    )

    service.write(focus_record)
    service.write(blocker_record)

    focus_results = service.read_by_category("current_focus")
    blocker_results = service.read_by_category("blocker")

    assert len(focus_results) == 1
    assert focus_results[0].type == "current_focus"
    assert focus_results[0].text == "Focus item."

    assert len(blocker_results) == 1
    assert blocker_results[0].type == "blocker"
    assert blocker_results[0].text == "Blocker item."


def test_state_service_corrupted_file(tmp_path: Path) -> None:
    """
    A corrupted (invalid JSON) file in the state directory should be skipped
    gracefully. read_all() should return an empty list and issue a warning,
    not raise an exception.
    """
    service = make_service(tmp_path)

    # Manually write a bad JSON file into the state directory.
    state_dir = tmp_path / "memory" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    corrupt_file = state_dir / f"{_recent_ts(3)}_current_focus.json"
    corrupt_file.write_text("{ this is not valid json }", encoding="utf-8")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        results = service.read_all()

    assert results == [], "Corrupted file should be skipped, returning empty list"
    assert any("Skipping" in str(w.message) for w in caught), (
        "Expected a warning about the unreadable file"
    )


# ---------------------------------------------------------------------------
# StateResolver tests
# ---------------------------------------------------------------------------

def test_state_resolver_latest_wins(tmp_path: Path) -> None:
    """
    When two records share the same category, StateResolver should return
    only the most recent one (latest timestamp wins).
    """
    service = make_service(tmp_path)

    older = StateRecord(
        id=_recent_ts(4),
        timestamp=_recent_ts(4),
        type="current_focus",
        text="Older focus.",
        source="test",
    )
    newer = StateRecord(
        id=_recent_ts(0),
        timestamp=_recent_ts(0),
        type="current_focus",
        text="Newer focus.",
        source="test",
    )

    service.write(older)
    service.write(newer)

    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    assert len(items) == 1
    assert items[0].category == "current_focus"
    assert items[0].text == "Newer focus."


def test_state_resolver_empty_vault(tmp_path: Path) -> None:
    """StateResolver on an empty vault should return an empty list without error."""
    resolver = make_resolver(tmp_path)

    items = resolver.get_current_state()

    assert items == []


def test_state_resolver_get_current_as_dict(tmp_path: Path) -> None:
    """
    get_current_as_dict() should return a dict keyed by category,
    with one entry per populated category.
    """
    service = make_service(tmp_path)

    service.write(StateRecord(
        id=_recent_ts(2),
        timestamp=_recent_ts(2),
        type="current_focus",
        text="Focus on state layer.",
        source="test",
    ))
    service.write(StateRecord(
        id=_recent_ts(1),
        timestamp=_recent_ts(1),
        type="open_loop",
        text="Follow up on eval harness.",
        source="test",
    ))

    resolver = StateResolver(service=service)
    state_dict = resolver.get_current_as_dict()

    assert "current_focus" in state_dict
    assert "open_loop" in state_dict
    assert state_dict["current_focus"].text == "Focus on state layer."
    assert state_dict["open_loop"].text == "Follow up on eval harness."
    assert isinstance(state_dict["current_focus"], StateItem)
    assert isinstance(state_dict["open_loop"], StateItem)


# ---------------------------------------------------------------------------
# Multi-record state categories (ADR-011)
# ---------------------------------------------------------------------------

def test_multiple_open_loops_returned(tmp_path: Path) -> None:
    """Two open_loop records should both appear in get_current_state()."""
    service = make_service(tmp_path)

    rec1 = StateService.make_record(
        state_type="open_loop",
        text="Fix the retrieval bug.",
        source="test",
    )
    rec1.timestamp = _recent_ts(2)
    service.write(rec1)

    rec2 = StateService.make_record(
        state_type="open_loop",
        text="Retest all local models.",
        source="test",
    )
    rec2.timestamp = _recent_ts(1)
    service.write(rec2)

    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    open_loops = [i for i in items if i.category == "open_loop"]
    assert len(open_loops) == 2
    texts = {i.text for i in open_loops}
    assert "Fix the retrieval bug." in texts
    assert "Retest all local models." in texts


def test_multiple_next_actions_returned(tmp_path: Path) -> None:
    """Two next_action records should both appear in get_current_state()."""
    service = make_service(tmp_path)

    rec1 = StateService.make_record(
        state_type="next_action",
        text="Lower extraction threshold.",
        source="test",
    )
    rec1.timestamp = _recent_ts(2)
    service.write(rec1)

    rec2 = StateService.make_record(
        state_type="next_action",
        text="Write commitment detector.",
        source="test",
    )
    rec2.timestamp = _recent_ts(1)
    service.write(rec2)

    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    actions = [i for i in items if i.category == "next_action"]
    assert len(actions) == 2


def test_single_record_categories_still_latest_wins(tmp_path: Path) -> None:
    """current_focus should still return only the latest record."""
    service = make_service(tmp_path)

    rec1 = StateService.make_record(
        state_type="current_focus",
        text="Old focus.",
        source="test",
    )
    rec1.timestamp = _recent_ts(2)
    service.write(rec1)

    rec2 = StateService.make_record(
        state_type="current_focus",
        text="New focus.",
        source="test",
    )
    rec2.timestamp = _recent_ts(1)
    service.write(rec2)

    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    focus_items = [i for i in items if i.category == "current_focus"]
    assert len(focus_items) == 1
    assert focus_items[0].text == "New focus."


def test_multi_record_capped_at_five(tmp_path: Path) -> None:
    """More than 5 open_loop records should be capped to 5 most recent."""
    service = make_service(tmp_path)

    for i in range(7):
        rec = StateService.make_record(
            state_type="open_loop",
            text=f"Loop {i}",
            source="test",
        )
        rec.timestamp = _recent_ts(10 - i)  # Loop 0 = oldest, Loop 6 = newest
        service.write(rec)

    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    open_loops = [i for i in items if i.category == "open_loop"]
    assert len(open_loops) == 5
    # Should be the 5 most recent (Loop 6, 5, 4, 3, 2)
    texts = {i.text for i in open_loops}
    assert "Loop 6" in texts
    assert "Loop 5" in texts
    assert "Loop 0" not in texts
    assert "Loop 1" not in texts


def test_resolved_open_loop_excluded(tmp_path: Path) -> None:
    """An open_loop with metadata.resolved=True should not appear."""
    service = make_service(tmp_path)

    rec1 = StateService.make_record(
        state_type="open_loop",
        text="Active loop.",
        source="test",
    )
    rec1.timestamp = _recent_ts(2)
    service.write(rec1)

    rec2 = StateService.make_record(
        state_type="open_loop",
        text="Resolved loop.",
        source="test",
        metadata={"resolved": True},
    )
    rec2.timestamp = _recent_ts(1)
    service.write(rec2)

    resolver = StateResolver(service=service)
    items = resolver.get_current_state()

    open_loops = [i for i in items if i.category == "open_loop"]
    assert len(open_loops) == 1
    assert open_loops[0].text == "Active loop."
