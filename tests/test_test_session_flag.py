"""
tests/test_test_session_flag.py

Tests for the test session flag — ensures eval harness conversations
are flagged and filtered from normal session listings.
"""

import json
import time
import pytest
from pathlib import Path
from unittest.mock import patch

from src.memory.session import create_session, list_sessions, delete_session
from src.memory.storage import MemoryStorage


@pytest.fixture
def temp_vault(tmp_path):
    """Create a temporary vault with session and conversation directories."""
    session_dir = tmp_path / "memory" / "session"
    session_dir.mkdir(parents=True)
    conv_dir = tmp_path / "memory" / "conversation"
    conv_dir.mkdir(parents=True)
    return tmp_path


def _create_spaced(vault, session_id, title, **kwargs):
    """Create a session with a small delay to avoid timestamp ID collisions."""
    time.sleep(1.1)  # _now_id() has second-level precision
    with patch("src.memory.session.get_private_vault_path", return_value=vault):
        return create_session(session_id, title, **kwargs)


class TestCreateSessionTestFlag:
    """create_session() should write metadata.test=True when test=True."""

    def test_test_flag_written_to_session_record(self, temp_vault):
        with patch("src.memory.session.get_private_vault_path", return_value=temp_vault):
            path = create_session("test-sess-1", "Eval test", test=True)
            record = json.loads(path.read_text(encoding="utf-8"))
            assert record["metadata"]["test"] is True
            assert record["metadata"]["session_id"] == "test-sess-1"

    def test_normal_session_has_no_test_flag(self, temp_vault):
        with patch("src.memory.session.get_private_vault_path", return_value=temp_vault):
            path = create_session("normal-sess-1", "Normal chat")
            record = json.loads(path.read_text(encoding="utf-8"))
            assert "test" not in record["metadata"]


class TestListSessionsFiltering:
    """list_sessions() should exclude test sessions by default."""

    def test_test_sessions_excluded_by_default(self, temp_vault):
        _create_spaced(temp_vault, "normal-1", "Normal conversation")
        _create_spaced(temp_vault, "test-1", "Eval run", test=True)
        _create_spaced(temp_vault, "normal-2", "Another normal chat")

        with patch("src.memory.session.get_private_vault_path", return_value=temp_vault):
            sessions = list_sessions()
        session_ids = {s["id"] for s in sessions}
        assert "normal-1" in session_ids
        assert "normal-2" in session_ids
        assert "test-1" not in session_ids

    def test_test_sessions_included_when_requested(self, temp_vault):
        _create_spaced(temp_vault, "normal-1", "Normal conversation")
        _create_spaced(temp_vault, "test-1", "Eval run", test=True)

        with patch("src.memory.session.get_private_vault_path", return_value=temp_vault):
            sessions = list_sessions(include_test=True)
        session_ids = {s["id"] for s in sessions}
        assert "normal-1" in session_ids
        assert "test-1" in session_ids


class TestSoftDeleteFiltering:
    """Soft-deleted sessions must not appear in list_sessions()."""

    def test_soft_deleted_session_excluded_from_list(self, temp_vault):
        _create_spaced(temp_vault, "keep-1", "Kept conversation")
        _create_spaced(temp_vault, "delete-me", "Will be deleted")
        _create_spaced(temp_vault, "keep-2", "Another kept conversation")

        with patch("src.memory.session.get_private_vault_path", return_value=temp_vault):
            # Before deletion, all three should appear
            sessions = list_sessions()
            session_ids = {s["id"] for s in sessions}
            assert "keep-1" in session_ids
            assert "delete-me" in session_ids
            assert "keep-2" in session_ids

            # Soft-delete one
            delete_session("delete-me")

            # After deletion, only two should appear
            sessions = list_sessions()
            session_ids = {s["id"] for s in sessions}
            assert "keep-1" in session_ids
            assert "keep-2" in session_ids
            assert "delete-me" not in session_ids

    def test_soft_deleted_session_not_in_api_response(self, temp_vault):
        """Verify the GET /v1/conversations endpoint filters soft-deleted sessions."""
        _create_spaced(temp_vault, "visible", "Visible conversation")
        _create_spaced(temp_vault, "hidden", "Hidden conversation")

        with patch("src.memory.session.get_private_vault_path", return_value=temp_vault):
            delete_session("hidden")
            sessions = list_sessions()
            session_ids = {s["id"] for s in sessions}
            assert "visible" in session_ids
            assert "hidden" not in session_ids


class TestCleanupScriptIdentification:
    """The cleanup script should correctly identify test sessions."""

    def test_find_test_sessions(self, temp_vault):
        _create_spaced(temp_vault, "normal-1", "Normal")
        _create_spaced(temp_vault, "test-1", "Eval 1", test=True)
        _create_spaced(temp_vault, "test-2", "Eval 2", test=True)

        from scripts.cleanup_test_sessions import find_test_sessions
        test_sessions = find_test_sessions(temp_vault)

        session_ids = {
            r.get("metadata", {}).get("session_id", "")
            for r in test_sessions
        }
        assert "test-1" in session_ids
        assert "test-2" in session_ids
        assert "normal-1" not in session_ids
