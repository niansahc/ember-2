"""
tests/test_record_write_lock.py

Session, project and lodestone edits read the latest version and write
the next one. The API runs sync endpoints on a thread pool, so two edits
of one record can overlap: without a lock both read version N and both
write N+1, and one edit is lost (a rename brings a deleted session back,
an update drops another update's field). Each store serializes its
read-then-write under one lock.

The race is forced, not left to timing: the outer edit's read returns,
then the inner edit runs on a second thread before the outer edit writes.
"""

import contextlib
import threading
from unittest.mock import patch

import pytest

import src.memory.lodestone_service as lodestone
import src.memory.project as project
import src.memory.session as session

SESSION_ID = "sess_test_001"


@pytest.fixture
def vault(tmp_path):
    with patch("src.memory.session.get_private_vault_path", return_value=tmp_path), \
         patch("src.memory.project.get_private_vault_path", return_value=tmp_path), \
         patch("src.memory.lodestone_service.get_private_vault_path", return_value=tmp_path):
        yield tmp_path


def _interleave(module, read_name, outer, inner):
    """Run `outer`. After its first call to module.<read_name> returns,
    start `inner` on a second thread and give it 0.3 s to finish before
    `outer` goes on to write."""
    real_read = getattr(module, read_name)
    threads = []

    def read(*args, **kwargs):
        result = real_read(*args, **kwargs)
        if not threads:
            threads.append(threading.Thread(target=inner))
            threads[0].start()
            threads[0].join(timeout=0.3)
        return result

    with patch.object(module, read_name, read):
        outer()
    threads[0].join(timeout=5)
    assert not threads[0].is_alive()


def _unlocked(module):
    return patch.object(module, "_write_lock", contextlib.nullcontext())


def _race_session():
    session.create_session(SESSION_ID, "first")
    _interleave(
        session, "get_session",
        outer=lambda: session.rename_session(SESSION_ID, "renamed"),
        inner=lambda: session.delete_session(SESSION_ID),
    )


def _race_project():
    project_id = project.create_project("first")["id"]
    _interleave(
        project, "get_project",
        outer=lambda: project.update_project(project_id, name="renamed"),
        inner=lambda: project.delete_project(project_id),
    )
    return project_id


def _race_lodestone():
    record_id = lodestone.write("honesty", "character")["id"]
    _interleave(
        lodestone, "_all_records",
        outer=lambda: lodestone.update(record_id, {"user_note": "note"}),
        inner=lambda: lodestone.update(record_id, {"flagged_as_noise": True}),
    )
    [current] = [r for r in lodestone.read_all() if r["id"] == record_id]
    return current


class TestOverlappingEditsAreSerialized:

    def test_session_rename_and_delete_stays_deleted(self, vault):
        _race_session()
        assert session.get_session(SESSION_ID) is None

    def test_project_update_and_delete_stays_deleted(self, vault):
        project_id = _race_project()
        assert project.get_project(project_id) is None

    def test_lodestone_overlapping_updates_keep_both_fields(self, vault):
        current = _race_lodestone()
        assert current["metadata"]["user_note"] == "note"
        assert current["metadata"]["flagged_as_noise"] is True
        assert current["version"] == 2


class TestControlWithoutTheLock:
    """Positive controls: the same forced interleaving with the lock
    removed loses an edit, so the tests above pass because of the lock."""

    def test_control_session_comes_back(self, vault):
        with _unlocked(session):
            _race_session()
        assert session.get_session(SESSION_ID)["text"] == "renamed"

    def test_control_project_comes_back(self, vault):
        with _unlocked(project):
            project_id = _race_project()
        assert project.get_project(project_id)["text"] == "renamed"

    def test_control_lodestone_loses_a_field(self, vault):
        with _unlocked(lodestone):
            current = _race_lodestone()
        meta = current["metadata"]
        assert current["version"] == 1
        assert not (meta["user_note"] == "note" and meta["flagged_as_noise"] is True)
