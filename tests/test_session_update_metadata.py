"""
tests/test_session_update_metadata.py

update_session() writes a new record with metadata rebuilt from scratch.
It used to drop metadata["test"], so renaming or moving an eval-harness
session made it appear in list_sessions() (ultrareview #280, item 2).

Runs against the session-scoped isolated test vault from conftest.py.
"""

from __future__ import annotations

import uuid

from src.memory.session import create_session, list_sessions, update_session


def _listed_ids(include_test: bool = False) -> set[str]:
    return {s["id"] for s in list_sessions(limit=100000, include_test=include_test)}


def test_renamed_test_session_stays_out_of_list_sessions():
    sid = f"sess_test_{uuid.uuid4().hex[:8]}"
    create_session(sid, "Synthetic eval session", test=True)

    update_session(sid, title="Renamed eval session")

    assert sid not in _listed_ids()
    assert sid in _listed_ids(include_test=True)


def test_moved_test_session_stays_out_of_list_sessions():
    sid = f"sess_test_{uuid.uuid4().hex[:8]}"
    create_session(sid, "Synthetic eval session", test=True)

    update_session(sid, project_id="proj_test_001")

    assert sid not in _listed_ids()


def test_control_renamed_regular_session_is_listed():
    """Positive control: list_sessions() does show an updated session when
    it is not a test session, so the absence above is the test flag at
    work, not a session that could never have been listed."""
    sid = f"sess_test_{uuid.uuid4().hex[:8]}"
    create_session(sid, "Synthetic regular session")

    update_session(sid, title="Renamed regular session")

    assert sid in _listed_ids()
