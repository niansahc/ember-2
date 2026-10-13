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

# ---------------------------------------------------------------------------
# Callers: a collision loses that one record, never the caller's whole job
# ---------------------------------------------------------------------------

OTHER_TS = "2026-01-01T12-00-01-000000"


class SequenceSource:
    """Timestamp source that returns `values` in order."""

    def __init__(self, *values: str) -> None:
        self._values = list(values)

    def next(self) -> str:
        return self._values.pop(0)


@pytest.fixture
def no_indexing(monkeypatch):
    """Indexing is not under test; skip the embedding call."""
    monkeypatch.setattr(write_memory_mod, "index_record", lambda *_a, **_k: None)


def test_json_import_collision_skips_that_record_and_imports_the_rest(vault, monkeypatch, no_indexing):
    from src.api.routes.ingest import _import_json_records

    monkeypatch.setattr(write_memory_mod, "_timestamps", SequenceSource(FIXED_TS, FIXED_TS, OTHER_TS))
    records = [
        {"text": f"synthetic journal entry number {i} for import", "type": "journal"}
        for i in range(3)
    ]

    result = _import_json_records(records)

    assert result["imported"] == 2
    assert result["errors"] == [{"index": 1, "error": "Record name collision; not written."}]
    names = sorted(p.name for p in (vault / "memory" / "journal").glob("*.json"))
    assert names == [f"{FIXED_TS}.json", f"{OTHER_TS}.json"]

SYNTHETIC_TURNS = [
    {"user": f"synthetic question {i}", "assistant": f"synthetic answer {i}"}
    for i in range(3)
]
SYNTHETIC_REFLECTION = "The user and Ember worked through a synthetic planning task and left one item open."


def test_session_reflection_collision_returns_none_without_raising(vault, monkeypatch, no_indexing, caplog):
    """Runs in a background thread on conversation delete: a collision must
    end in a log line, not an unhandled thread exception."""
    import src.reflection.session_reflection as session_reflection

    monkeypatch.setattr(
        session_reflection.ollama, "chat",
        lambda **_k: {"message": {"content": SYNTHETIC_REFLECTION}},
    )
    _taken_name(vault, monkeypatch, write_memory_mod, "_timestamps", "reflection")

    with caplog.at_level(logging.ERROR):
        result = session_reflection.generate_session_reflection(
            SYNTHETIC_TURNS, session_id=SESSION_ID, model="synthetic-local-model",
        )

    assert result is None
    assert any("[SESSION_REFLECT] Reflection not written" in r.getMessage() for r in caplog.records)

def test_session_summary_collision_still_injects_the_summary_turn(vault, monkeypatch, no_indexing, caplog):
    """Buffer compression pops the oldest turns before the summary write. A
    collision on that write must not lose them: the summary turn still
    replaces them in the buffer."""
    from unittest.mock import MagicMock

    from src.context.conversation_buffer import ConversationBuffer
    from src.llm.adapter import LLMAdapter
    from src.memory.service import MemoryService

    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "synthetic-local-model"
    adapter.prompt_builder = MagicMock()
    adapter.prompt_builder.conversation_buffer = ConversationBuffer()
    adapter.memory_service = MemoryService()
    buf = adapter.prompt_builder.conversation_buffer
    long_msg = " ".join(f"word{j}" for j in range(100))
    for _ in range(8):
        buf.add_turn(long_msg, long_msg)
    monkeypatch.setattr(adapter, "_summarize_with_plain_prompt", lambda _p: SYNTHETIC_REFLECTION)
    _taken_name(vault, monkeypatch, write_memory_mod, "_timestamps", "reflection")

    with caplog.at_level(logging.ERROR):
        adapter._maybe_compress_buffer()

    first = buf.get_recent()[0]
    assert first.get("is_summary") is True
    assert first["assistant"] == SYNTHETIC_REFLECTION
    assert any("[BUFFER] Session summary not written" in r.getMessage() for r in caplog.records)

def test_onboarding_profile_collision_reasks_the_same_step(vault, monkeypatch, no_indexing, caplog):
    """A raise here would reach a streaming chat client as a JSON 409 (blank
    reply). The answer is lost, so the step is not advanced and its question
    is asked again."""
    from src.onboarding.service import OnboardingService
    from src.onboarding.steps import ONBOARDING_STEPS
    from src.state.state_service import StateService

    service = OnboardingService()
    service._state = StateService(vault_path=vault)
    service._write_progress(step=0, completed=[])
    monkeypatch.setattr(
        service, "_format_answer",
        lambda answer, _category: f"The user said this in a synthetic onboarding answer: {answer}",
    )
    _taken_name(vault, monkeypatch, write_memory_mod, "_timestamps", "profile")

    with caplog.at_level(logging.ERROR):
        reply = service.handle("synthetic answer")

    assert reply == ONBOARDING_STEPS[0].question
    progress = service._state.read_by_category("onboarding")
    assert len(progress) == 1 and progress[0].metadata["current_step"] == 0
    assert any("[ONBOARDING] Profile write collided" in r.getMessage() for r in caplog.records)

def test_journal_cli_collision_exits_1_with_a_message(vault, monkeypatch, no_indexing, capsys):
    import sys

    import scripts.journal as journal

    target = _taken_name(vault, monkeypatch, write_memory_mod, "_timestamps", "journal")
    monkeypatch.setattr(
        sys, "argv", ["journal.py", "--text", "synthetic journal entry for a collision test"],
    )

    with pytest.raises(SystemExit) as exit_info:
        journal.main()

    assert exit_info.value.code == 1
    assert "already exists" in capsys.readouterr().err
    assert json.loads(target.read_text(encoding="utf-8")) == PLACEHOLDER

def test_seed_script_collision_counts_a_failure_and_seeds_the_rest(vault, monkeypatch, no_indexing, capsys):
    import scripts.seed_identity_template as seed

    _taken_name(vault, monkeypatch, write_memory_mod, "_timestamps", "profile")
    monkeypatch.setattr(write_memory_mod, "_timestamps", SequenceSource(FIXED_TS, OTHER_TS))
    monkeypatch.setattr(seed, "PROFILE_RECORDS", [
        {"text": "The user is a synthetic profile subject number one for seeding.", "tags": []},
        {"text": "The user is a synthetic profile subject number two for seeding.", "tags": []},
    ])

    seed.seed_identity()

    assert "1 written, 0 skipped, 1 failed" in capsys.readouterr().out
    assert (vault / "memory" / "profile" / f"{OTHER_TS}.json").exists()

# ---------------------------------------------------------------------------
# API routes: a collision is a retryable 409, not a 500
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client(vault, monkeypatch, no_indexing):
    from fastapi.testclient import TestClient

    import src.api.main as main

    monkeypatch.setattr(main, "get_ember_api_key", lambda: None)
    return TestClient(main.app)


def test_journal_route_collision_returns_409(api_client, vault, monkeypatch):
    _taken_name(vault, monkeypatch, write_memory_mod, "_timestamps", "journal")

    response = api_client.post("/journal", json={"text": "synthetic journal entry for a collision test"})

    assert response.status_code == 409


def test_conversation_rename_collision_returns_409(api_client, vault, monkeypatch):
    _make_session()
    _taken_name(vault, monkeypatch, session_mod, "_ids", "session")

    response = api_client.patch(f"/v1/conversations/{SESSION_ID}", json={"title": "Renamed synthetic"})

    assert response.status_code == 409


def test_project_create_collision_returns_409(api_client, vault, monkeypatch):
    _taken_name(vault, monkeypatch, project_mod, "_ids", "project")

    response = api_client.post("/v1/projects", json={"name": "Synthetic project"})

    assert response.status_code == 409


def test_lodestone_update_collision_returns_409(api_client, vault, monkeypatch):
    rec_id = _make_lodestone()
    _taken_name(vault, monkeypatch, lodestone_service, "_timestamps", "lodestone")

    response = api_client.patch(f"/v1/lodestone/{rec_id}", json={"user_note": "synthetic note"})

    assert response.status_code == 409