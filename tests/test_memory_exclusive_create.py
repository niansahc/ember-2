"""
tests/test_memory_exclusive_create.py

Memory, session, project and lodestone records are append-only, and each
writer names its file `{timestamp}.json`. UniqueTimestampSource keeps names
unique inside one process, but a second process on the same vault (a script
next to the API) can pick the same name. These writers used to publish with
os.replace, so the later write silently replaced the earlier record.

They now create the file exclusively (MemoryStorage.create_json ->
safe_create_json) and raise RecordExistsError on a taken name, after logging
it. Each writer's timestamp source is pinned here to force the collision.

All records here are synthetic; every vault is a tmp_path.
"""

from __future__ import annotations

import json
import logging

import pytest

import src.memory.lodestone_service as lodestone_service
import src.memory.project as project_mod
import src.memory.session as session_mod
import src.memory.write_memory as write_memory_mod
from src.core.config import vault_binding
from src.core.jsonio import RecordExistsError, safe_write_json
from src.memory.storage import MemoryStorage
from tests.conftest import FixedSource, run_in_threads

FIXED_TS = "2026-01-01T12-00-00-000000"
SESSION_ID = "sess_test_001"


# Each writer: (module holding its timestamp source, source attribute,
# vault subdirectory, setup run before the source is pinned, the write).
# setup returns whatever the write needs (an existing session, project or
# lodestone record to update).

def _no_setup():
    return None


def _make_session():
    session_mod.create_session(SESSION_ID, "Synthetic conversation")


def _make_project():
    return project_mod.create_project("Synthetic project")["id"]


def _make_lodestone():
    return lodestone_service.write("synthetic value", "synthetic_category", confirmed=False)["id"]


WRITERS = [
    pytest.param(
        write_memory_mod, "_timestamps", "journal", _no_setup,
        lambda _: write_memory_mod.write_canonical_record(
            "synthetic journal entry for a collision test", memory_type="journal",
        ),
        id="canonical_record",
    ),
    pytest.param(
        session_mod, "_ids", "session", _no_setup,
        lambda _: session_mod.create_session(SESSION_ID, "Synthetic conversation"),
        id="create_session",
    ),
    pytest.param(
        session_mod, "_ids", "session", _make_session,
        lambda _: session_mod.update_session(SESSION_ID, title="Renamed synthetic"),
        id="update_session",
    ),
    pytest.param(
        session_mod, "_ids", "session", _make_session,
        lambda _: session_mod.delete_session(SESSION_ID),
        id="delete_session",
    ),
    pytest.param(
        project_mod, "_ids", "project", _no_setup,
        lambda _: project_mod.create_project("Synthetic project"),
        id="create_project",
    ),
    pytest.param(
        project_mod, "_ids", "project", _make_project,
        lambda pid: project_mod.update_project(pid, name="Renamed synthetic"),
        id="update_project",
    ),
    pytest.param(
        project_mod, "_ids", "project", _make_project,
        lambda pid: project_mod.delete_project(pid),
        id="delete_project",
    ),
    pytest.param(
        lodestone_service, "_timestamps", "lodestone", _no_setup,
        lambda _: lodestone_service.write("synthetic value", "synthetic_category", confirmed=False),
        id="lodestone_write",
    ),
    pytest.param(
        lodestone_service, "_timestamps", "lodestone", _make_lodestone,
        lambda rec_id: lodestone_service.update(rec_id, {"user_note": "synthetic note"}),
        id="lodestone_update",
    ),
]

WRITER_ARGS = "module, source_attr, subdir, setup, write"

# Holds the taken name. Its type matches no writer's resolver, so it never
# changes what get_session/get_project/lodestone reads return.
PLACEHOLDER = {"id": "placeholder", "type": "placeholder", "text": "existing synthetic record"}


@pytest.fixture
def vault(tmp_path):
    with vault_binding(tmp_path):
        yield tmp_path


def _taken_name(vault_path, monkeypatch, module, source_attr, subdir):
    """Pin the writer's timestamp source and occupy the name it will pick."""
    monkeypatch.setattr(module, source_attr, FixedSource(FIXED_TS))
    target = vault_path / "memory" / subdir / f"{FIXED_TS}.json"
    safe_write_json(target, PLACEHOLDER)
    return target


# ---------------------------------------------------------------------------
# Sequential collision: raise, leave the existing record untouched, log
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(WRITER_ARGS, WRITERS)
def test_write_to_a_taken_name_raises_and_keeps_the_existing_record(
    vault, monkeypatch, caplog, module, source_attr, subdir, setup, write,
):
    arg = setup()
    target = _taken_name(vault, monkeypatch, module, source_attr, subdir)
    original = target.read_bytes()
    files_before = sorted((vault / "memory" / subdir).glob("*.json"))

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RecordExistsError):
            write(arg)

    assert target.read_bytes() == original
    assert sorted((vault / "memory" / subdir).glob("*.json")) == files_before
    assert any(
        "Filename collision" in r.getMessage() and f"{subdir}/{FIXED_TS}.json" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Concurrent collision: two writers pick one name, exactly one succeeds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(WRITER_ARGS, WRITERS)
def test_concurrent_writers_to_one_name_exactly_one_succeeds(
    vault, monkeypatch, slow_temp_files, module, source_attr, subdir, setup, write,
):
    arg = setup()
    monkeypatch.setattr(module, source_attr, FixedSource(FIXED_TS))
    files_before = set((vault / "memory" / subdir).glob("*.json"))

    def bound_write(_i):
        with vault_binding(vault):
            return write(arg)

    results, errors = run_in_threads(bound_write, 2)

    assert len(results) == 1
    assert len(errors) == 1 and isinstance(errors[0], RecordExistsError)
    new_files = set((vault / "memory" / subdir).glob("*.json")) - files_before
    assert [p.name for p in new_files] == [f"{FIXED_TS}.json"]


# ---------------------------------------------------------------------------
# Positive controls: the old replace-on-write path, same harness
# ---------------------------------------------------------------------------

@pytest.fixture
def old_replace_path(monkeypatch):
    """Route create_json through write_json (os.replace), as before."""
    monkeypatch.setattr(MemoryStorage, "create_json", MemoryStorage.write_json)


def test_control_old_path_replaces_the_taken_record_without_raising(vault, monkeypatch, old_replace_path):
    """The sequential test's raises() and byte-identical checks can fail:
    under the old path the write succeeds and the existing record is gone."""
    target = _taken_name(vault, monkeypatch, write_memory_mod, "_timestamps", "journal")

    write_memory_mod.write_canonical_record(
        "synthetic journal entry for a collision test", memory_type="journal",
    )

    assert json.loads(target.read_text(encoding="utf-8"))["type"] == "journal"


def test_control_old_path_never_reports_a_concurrent_collision(
    vault, monkeypatch, slow_temp_files, old_replace_path,
):
    """The concurrent test's RecordExistsError check can fail: under the old
    path neither writer raises it (both replace, or Windows refuses one
    os.replace with a plain write error)."""
    monkeypatch.setattr(project_mod, "_ids", FixedSource(FIXED_TS))

    def bound_write(_i):
        with vault_binding(vault):
            return project_mod.create_project("Synthetic project")

    _results, errors = run_in_threads(bound_write, 2)

    assert not any(isinstance(e, RecordExistsError) for e in errors)