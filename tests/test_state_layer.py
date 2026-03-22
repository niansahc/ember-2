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
from pathlib import Path

import pytest

from src.state.models import StateItem, StateRecord
from src.state.state_resolver import StateResolver
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
        id="2026-03-21T10-00-00",
        timestamp="2026-03-21T10-00-00",
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
            id="2026-03-21T10-00-00",
            timestamp="2026-03-21T10-00-00",
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
        id="2026-03-21T10-00-00",
        timestamp="2026-03-21T10-00-00",
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
        id="2026-03-21T10-00-00",
        timestamp="2026-03-21T10-00-00",
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
        id="2026-03-21T10-00-00",
        timestamp="2026-03-21T10-00-00",
        type="current_focus",
        text="Focus item.",
        source="test",
    )
    blocker_record = StateRecord(
        id="2026-03-21T11-00-00",
        timestamp="2026-03-21T11-00-00",
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
    corrupt_file = state_dir / "2026-03-21T09-00-00_current_focus.json"
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
        id="2026-03-21T08-00-00",
        timestamp="2026-03-21T08-00-00",
        type="current_focus",
        text="Older focus.",
        source="test",
    )
    newer = StateRecord(
        id="2026-03-21T14-00-00",
        timestamp="2026-03-21T14-00-00",
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
        id="2026-03-21T10-00-00",
        timestamp="2026-03-21T10-00-00",
        type="current_focus",
        text="Focus on state layer.",
        source="test",
    ))
    service.write(StateRecord(
        id="2026-03-21T11-00-00",
        timestamp="2026-03-21T11-00-00",
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
