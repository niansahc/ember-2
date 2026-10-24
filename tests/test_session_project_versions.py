"""
tests/test_session_project_versions.py

Session and project records are append-only: every rename, move, recolor
or delete writes a new record, and the resolver picks the latest per
session_id / project_id. "Latest" is the record's metadata.version, which
each write sets one above the version it built on. The record timestamp
(raw wall clock) only orders records written before the counter existed:
a clock that reads the same value twice, or steps backward (NTP
correction, a changed system clock), must not resurrect an older record.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import src.memory.project as project
import src.memory.session as session

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
SESSION_ID = "sess_test_001"


@pytest.fixture
def vault(tmp_path):
    with patch("src.memory.session.get_private_vault_path", return_value=tmp_path), \
         patch("src.memory.project.get_private_vault_path", return_value=tmp_path):
        yield tmp_path


class _FrozenClock:
    """Stands in for the module's `datetime` so record timestamps read `at`."""

    def __init__(self, at):
        self._at = at

    def now(self, tz=None):
        return self._at


def _at(module, when):
    return patch.object(module, "datetime", _FrozenClock(when))


@pytest.mark.parametrize("step", [timedelta(0), timedelta(seconds=-2)], ids=["tied", "inverted"])
class TestLatestVersionDoesNotDependOnTimestamp:

    def test_session_rename_then_delete_stays_deleted(self, vault, step):
        with _at(session, T0):
            session.create_session(SESSION_ID, "first")
        with _at(session, T0 + step):
            session.rename_session(SESSION_ID, "renamed")
        with _at(session, T0 + 2 * step):
            session.delete_session(SESSION_ID)

        assert session.get_session(SESSION_ID) is None

    def test_session_rename_wins(self, vault, step):
        with _at(session, T0):
            session.create_session(SESSION_ID, "first")
        with _at(session, T0 + step):
            session.rename_session(SESSION_ID, "renamed")

        assert session.get_session(SESSION_ID)["text"] == "renamed"

    def test_project_update_wins(self, vault, step):
        with _at(project, T0):
            project_id = project.create_project("first")["id"]
        with _at(project, T0 + step):
            project.update_project(project_id, name="renamed")

        assert project.get_project(project_id)["text"] == "renamed"

    def test_project_update_then_delete_stays_deleted(self, vault, step):
        with _at(project, T0):
            project_id = project.create_project("first")["id"]
        with _at(project, T0 + step):
            project.update_project(project_id, name="renamed")
        with _at(project, T0 + 2 * step):
            project.delete_project(project_id)

        assert project.get_project(project_id) is None


class TestVersionCounter:

    def test_session_versions_increase_by_one_per_write(self, vault):
        session.create_session(SESSION_ID, "first")
        session.rename_session(SESSION_ID, "second")
        session.update_session(SESSION_ID, project_id="proj_test_001")
        session.delete_session(SESSION_ID)

        records = session._read_all_session_records()
        assert sorted(r["metadata"]["version"] for r in records) == [0, 1, 2, 3]

    def test_project_versions_increase_by_one_per_write(self, vault):
        project_id = project.create_project("first")["id"]
        project.update_project(project_id, color="#000000")
        project.delete_project(project_id)

        records = project._read_all_project_records()
        assert sorted(r["metadata"]["version"] for r in records) == [0, 1, 2]


class TestControlUnversionedLegacyRecords:
    """Positive control: records written before the version counter exist
    still resolve by timestamp, so the version path above is what decides
    in the tests that pass, not the timestamp."""

    def test_session_legacy_records_order_by_timestamp(self):
        records = [
            {"timestamp": "2026-01-01T12:00:02", "text": "new", "metadata": {"session_id": SESSION_ID}},
            {"timestamp": "2026-01-01T12:00:01", "text": "old", "metadata": {"session_id": SESSION_ID}},
        ]
        assert session._resolve_sessions(records)[SESSION_ID]["text"] == "new"
        assert session._resolve_sessions(records[::-1])[SESSION_ID]["text"] == "new"

    def test_project_legacy_records_order_by_timestamp(self):
        records = [
            {"timestamp": "2026-01-01T12:00:02", "text": "new", "metadata": {"project_id": "proj_test_001"}},
            {"timestamp": "2026-01-01T12:00:01", "text": "old", "metadata": {"project_id": "proj_test_001"}},
        ]
        assert project._resolve_projects(records)["proj_test_001"]["text"] == "new"
        assert project._resolve_projects(records[::-1])["proj_test_001"]["text"] == "new"
