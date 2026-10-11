"""
tests/test_update_change_logging.py

update_session() and update_project() log whether the title/name changed.
The check used the resolved value (new_title / new_name), which falls back
to the existing title and is never None, so the log always said True
(ultrareview #280, item 5). The check must use the argument the caller
supplied.

Each False case is paired with a True case on the same call path, so the
False assertion cannot pass merely because the field is never logged.
"""

from __future__ import annotations

import logging
import uuid

from src.memory.project import create_project, update_project
from src.memory.session import create_session, update_session


def _messages(caplog, logger_name: str) -> str:
    return "\n".join(r.getMessage() for r in caplog.records if r.name == logger_name)


def _new_session() -> str:
    sid = f"sess_test_{uuid.uuid4().hex[:8]}"
    create_session(sid, "Synthetic session", test=True)
    return sid


def test_session_move_without_title_logs_title_unchanged(caplog):
    sid = _new_session()
    with caplog.at_level(logging.INFO, logger="ember.session"):
        update_session(sid, project_id="proj_test_001")

    assert "title_changed=False" in _messages(caplog, "ember.session")


def test_control_session_rename_logs_title_changed(caplog):
    sid = _new_session()
    with caplog.at_level(logging.INFO, logger="ember.session"):
        update_session(sid, title="Renamed synthetic session")

    assert "title_changed=True" in _messages(caplog, "ember.session")


def test_project_recolor_without_name_logs_name_unchanged(caplog):
    project_id = create_project("Synthetic project")["id"]
    with caplog.at_level(logging.INFO, logger="ember.project"):
        update_project(project_id, color="#123456")

    assert "name_changed=False" in _messages(caplog, "ember.project")


def test_control_project_rename_logs_name_changed(caplog):
    project_id = create_project("Synthetic project")["id"]
    with caplog.at_level(logging.INFO, logger="ember.project"):
        update_project(project_id, name="Renamed synthetic project")

    assert "name_changed=True" in _messages(caplog, "ember.project")
