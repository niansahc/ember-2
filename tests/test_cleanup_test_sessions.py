"""
tests/test_cleanup_test_sessions.py

scripts/cleanup_test_sessions.py picks the latest record per session the
way src.memory.session does (highest metadata.version; the timestamp only
orders records written before the counter) and soft-deletes through
session.delete_session, so its delete record is one version above the
record it deletes. A clock that repeats a reading or steps backward must
not leave a session the script reported deleted still listed.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest

import scripts.cleanup_test_sessions as cleanup
import src.memory.session as session
from tests.test_session_project_versions import SESSION_ID, T0, _at


@pytest.fixture
def vault(tmp_path):
    with patch("src.memory.session.get_private_vault_path", return_value=tmp_path):
        yield tmp_path


def _latest_record(vault):
    records = [session.storage.read_json(f) for f in session.storage.list_memory_files(vault / "memory" / "session")]
    return session._resolve_sessions(records)[SESSION_ID]


@pytest.mark.parametrize("step", [timedelta(0), timedelta(seconds=-2)], ids=["tied", "inverted"])
class TestCleanupFollowsVersionOrder:

    def test_finds_the_latest_version(self, vault, step):
        with _at(session, T0):
            session.create_session(SESSION_ID, "first", test=True)
        with _at(session, T0 + step):
            session.rename_session(SESSION_ID, "renamed")

        assert [r["text"] for r in cleanup.find_test_sessions(vault)] == ["renamed"]

    def test_deleted_session_stays_deleted(self, vault, step):
        with _at(session, T0):
            session.create_session(SESSION_ID, "first", test=True)
        with _at(session, T0 + step):
            session.rename_session(SESSION_ID, "renamed")

        with _at(session, T0 + 2 * step):
            for rec in cleanup.find_test_sessions(vault):
                cleanup.soft_delete_session(rec["metadata"]["session_id"])

        assert session.get_session(SESSION_ID) is None
        latest = _latest_record(vault)
        assert latest["metadata"]["version"] == 2
        assert latest["source"] == "cleanup_script"

    def test_already_deleted_session_is_not_found(self, vault, step):
        with _at(session, T0):
            session.create_session(SESSION_ID, "first", test=True)
        with _at(session, T0 + step):
            session.delete_session(SESSION_ID)

        assert cleanup.find_test_sessions(vault) == []


class TestControls:
    """Positive controls for the absence assertions above."""

    def test_control_unbumped_delete_record_loses_to_the_rename(self, vault):
        """The delete record the script wrote before this fix (a copy of the
        resolved record's metadata, version unchanged) leaves the session
        listed when the clock stepped back, so get_session() is None above
        because the delete record carries the next version."""
        with _at(session, T0):
            session.create_session(SESSION_ID, "first", test=True)
        with _at(session, T0 - timedelta(seconds=2)):
            session.rename_session(SESSION_ID, "renamed")
        stale = min(
            (session.storage.read_json(f) for f in session.storage.list_memory_files(vault / "memory" / "session")),
            key=lambda r: r["metadata"]["version"],
        )
        tombstone = dict(
            stale,
            id="2026-01-01T12-00-05-000000",
            timestamp="2026-01-01T12:00:05+00:00",
            metadata=dict(stale["metadata"], deleted=True),
        )
        session.storage.create_json(vault / "memory" / "session" / f"{tombstone['id']}.json", tombstone)

        assert session.get_session(SESSION_ID)["text"] == "renamed"

    def test_control_live_test_session_is_found(self, vault):
        with _at(session, T0):
            session.create_session(SESSION_ID, "first", test=True)

        assert [r["text"] for r in cleanup.find_test_sessions(vault)] == ["first"]

    def test_control_unversioned_records_order_by_timestamp(self, vault):
        """Records written before the counter still resolve by timestamp:
        a delete record with the later timestamp hides the session, one
        with the earlier timestamp does not."""
        session_dir = vault / "memory" / "session"
        session_dir.mkdir(parents=True)

        def legacy(name, stamp, deleted):
            session.storage.create_json(session_dir / f"{name}.json", {
                "id": name, "timestamp": stamp, "type": "session", "text": name,
                "metadata": {"session_id": SESSION_ID, "deleted": deleted, "test": True},
            })

        legacy("live", "2026-01-01T12:00:01", False)
        legacy("gone", "2026-01-01T12:00:02", True)
        assert cleanup.find_test_sessions(vault) == []

        legacy("revived", "2026-01-01T12:00:03", False)
        assert [r["text"] for r in cleanup.find_test_sessions(vault)] == ["revived"]
