"""
tests/test_record_exclusive_create.py

StateService.write and TaskService.write used to check `file_path.exists()`
and, on a taken name, warn and return the existing path: the new record was
silently dropped and the caller believed it was written. The check was also
separate from the publish (os.replace), so two writers could both pass it and
the second silently overwrote the first.

Both now create the file exclusively through src.core.jsonio.safe_create_json
(temp file + os.link, one atomic step) and raise RecordExistsError on a taken
name, after logging the collision. Callers decide what a collision means:
the API returns 409, background extraction loses only that record,
onboarding logs and continues.

All records here are synthetic; every vault is a tmp_path.
"""

from __future__ import annotations

import errno
import json
import logging
import os
import sys
import tempfile
import time

import pytest

import src.core.jsonio as jsonio
import src.state.state_service as state_service_mod
import src.tasks.task_service as task_service_mod
from src.core.config import vault_binding
from src.core.jsonio import RecordExistsError, safe_create_json, safe_write_json
from src.state.models import StateRecord
from src.state.state_service import StateService
from src.tasks.task_service import TaskService
from tests.conftest import run_in_threads

FIXED_TS = "2026-01-01T12-00-00"


def _state_record(text: str) -> StateRecord:
    return StateRecord(
        id=FIXED_TS, timestamp=FIXED_TS, type="open_loop",
        text=text, source="test",
    )


def _task_record(text: str):
    record = TaskService.make_record(title="Synthetic task", status="active", source="test", text=text)
    record.timestamp = FIXED_TS
    return record


SERVICES = [
    pytest.param(StateService, _state_record, "memory/state", "[STATE_SERVICE]", id="state"),
    pytest.param(TaskService, _task_record, "memory/task", "[TASK_SERVICE]", id="task"),
]


def _files(root, subdir) -> list:
    return sorted((root / subdir).glob("*.json"))


# ---------------------------------------------------------------------------
# Sequential collision: raise, keep the first record, log
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("service_cls, make, subdir, tag", SERVICES)
def test_second_record_with_a_taken_name_raises_and_keeps_the_first(
    tmp_path, caplog, service_cls, make, subdir, tag,
):
    service = service_cls(vault_path=tmp_path)
    first_path = service.write(make("first synthetic record"))
    original = first_path.read_bytes()

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RecordExistsError):
            service.write(make("second synthetic record"))

    assert _files(tmp_path, subdir) == [first_path]
    assert first_path.read_bytes() == original
    assert any(
        tag in r.getMessage() and "Filename collision" in r.getMessage()
        for r in caplog.records
    )


def test_task_collision_log_carries_no_title(tmp_path, caplog):
    """The task filename holds a slug of the title (user content); the
    collision log names the timestamp only."""
    service = TaskService(vault_path=tmp_path)
    service.write(_task_record("one"))

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RecordExistsError):
            service.write(_task_record("two"))

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert FIXED_TS in messages
    assert "synthetic-task" not in messages


# ---------------------------------------------------------------------------
# Concurrent collision: the existence check and the publish are one step
# ---------------------------------------------------------------------------

@pytest.fixture
def slow_temp_files(monkeypatch):
    """Every temp file creation waits 50 ms, so concurrent writers have all
    passed any pre-publish existence check before the first one publishes."""
    real_mkstemp = tempfile.mkstemp

    def slow_mkstemp(*args, **kwargs):
        time.sleep(0.05)
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(tempfile, "mkstemp", slow_mkstemp)


@pytest.mark.parametrize("service_cls, make, subdir, tag", SERVICES)
def test_concurrent_writers_to_one_name_exactly_one_succeeds(
    tmp_path, slow_temp_files, service_cls, make, subdir, tag,
):
    service = service_cls(vault_path=tmp_path)
    records = [make(f"concurrent synthetic record {i}") for i in range(2)]

    paths, errors = run_in_threads(lambda i: service.write(records[i]), 2)

    assert len([p for p in paths if p is not None]) == 1
    assert len(errors) == 1
    assert isinstance(errors[0], RecordExistsError)
    assert len(_files(tmp_path, subdir)) == 1


def test_control_check_then_replace_never_reports_the_collision(tmp_path, slow_temp_files):
    """Positive control: the previous exists() + safe_write_json sequence in
    the same harness lets both writers past the check. Both os.replace calls
    then either succeed (one record silently overwritten) or, on Windows,
    one fails with a PermissionError surfaced as JsonIoError. Neither outcome
    is a RecordExistsError, so the assertions above can fail."""
    target = tmp_path / "record.json"

    def old_write(i: int):
        if target.exists():
            return target
        return safe_write_json(target, {"n": i})

    _paths, errors = run_in_threads(old_write, 2)

    assert not any(isinstance(e, RecordExistsError) for e in errors)
    assert all(isinstance(e, jsonio.JsonIoError) for e in errors)
    assert len(list(tmp_path.glob("*.json"))) == 1


# ---------------------------------------------------------------------------
# safe_create_json
# ---------------------------------------------------------------------------

def test_safe_create_json_writes_a_new_file(tmp_path):
    target = tmp_path / "sub" / "record.json"

    assert safe_create_json(target, {"n": 1}) == target
    assert json.loads(target.read_text(encoding="utf-8")) == {"n": 1}
    assert [p.name for p in target.parent.iterdir()] == ["record.json"]


def test_safe_create_json_refuses_a_taken_name_and_leaves_no_temp(tmp_path):
    target = tmp_path / "record.json"
    safe_create_json(target, {"n": 1})

    with pytest.raises(RecordExistsError):
        safe_create_json(target, {"n": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"n": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["record.json"]


def test_safe_create_json_without_hard_links_falls_back_to_exclusive_create(
    tmp_path, monkeypatch, caplog,
):
    def no_links(src, dst):
        raise OSError(errno.EPERM, "hard links not supported")

    monkeypatch.setattr(os, "link", no_links)
    monkeypatch.setattr(jsonio, "_link_fallback_logged", False)
    target = tmp_path / "record.json"

    with caplog.at_level(logging.WARNING, logger="ember.jsonio"):
        safe_create_json(target, {"n": 1})
        with pytest.raises(RecordExistsError):
            safe_create_json(target, {"n": 2})

    assert json.loads(target.read_text(encoding="utf-8")) == {"n": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["record.json"]
    fallback_logs = [r for r in caplog.records if "falls back to O_EXCL" in r.getMessage()]
    assert len(fallback_logs) == 1


# ---------------------------------------------------------------------------
# Callers
# ---------------------------------------------------------------------------

class _FixedSource:
    def __init__(self, value: str) -> None:
        self.value = value

    def next(self) -> str:
        return self.value


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import src.api.main as main

    monkeypatch.setattr(main, "get_ember_api_key", lambda: None)
    monkeypatch.setattr(state_service_mod, "_state_timestamps", _FixedSource(FIXED_TS))
    monkeypatch.setattr(task_service_mod, "_timestamps", _FixedSource(FIXED_TS + "-000000"))
    with vault_binding(tmp_path):
        yield TestClient(main.app)


def test_write_state_collision_returns_409(api_client, tmp_path):
    body = {"type": "open_loop", "text": "Synthetic loop", "source": "test"}

    first = api_client.post("/write-state", json=body)
    second = api_client.post("/write-state", json=body)

    assert first.status_code == 200
    assert second.status_code == 409
    assert len(_files(tmp_path, "memory/state")) == 1


def test_create_task_collision_returns_409(api_client, tmp_path):
    first = api_client.post("/v1/tasks", json={"title": "Synthetic task"})
    second = api_client.post("/v1/tasks", json={"title": "Synthetic task"})

    assert first.status_code == 200
    assert second.status_code == 409
    assert len(_files(tmp_path, "memory/task")) == 1


def test_background_extraction_keeps_writing_after_a_collision(tmp_path, monkeypatch):
    """The first extracted record's name is taken; the second record of the
    turn is still written."""
    import src.api.openai_adapter as adapter

    taken = _state_record("already in the vault")
    fresh = StateRecord(
        id="2026-01-01T12-00-01", timestamp="2026-01-01T12-00-01",
        type="open_loop", text="new synthetic loop", source="state_extractor",
    )
    colliding = _state_record("same name as the existing record")
    monkeypatch.setattr(
        adapter.state_extractor, "extract",
        lambda *_a, **_k: [colliding, fresh],
    )

    with vault_binding(tmp_path):
        StateService().write(taken)
        adapter._background_state_extraction("synthetic user message", "synthetic reply")

    names = [p.name for p in _files(tmp_path, "memory/state")]
    assert names == [f"{FIXED_TS}_open_loop.json", "2026-01-01T12-00-01_open_loop.json"]


def test_onboarding_state_collision_does_not_fail_the_turn(tmp_path, monkeypatch, caplog):
    from src.onboarding.service import OnboardingService

    service = OnboardingService()
    service._state = StateService(vault_path=tmp_path)
    monkeypatch.setattr(state_service_mod, "_state_timestamps", _FixedSource(FIXED_TS))
    service._state.write(StateService.make_record(
        state_type="onboarding", text="onboarding_in_progress", source="test",
    ))
    # read_by_category must see no onboarding record so handle() takes the
    # first-call path and writes step 0 under the taken name.
    monkeypatch.setattr(service._state, "read_by_category", lambda _category: [])

    with caplog.at_level(logging.ERROR):
        reply = service.handle("hello")

    assert reply.startswith("Welcome to Ember.")
    assert any("[ONBOARDING] State write collided" in r.getMessage() for r in caplog.records)


def test_add_state_cli_collision_exits_1_with_a_message(tmp_path, monkeypatch, capsys):
    import scripts.add_state as add_state

    monkeypatch.setattr(state_service_mod, "_state_timestamps", _FixedSource(FIXED_TS))
    monkeypatch.setattr(sys, "argv", ["add_state.py", "--type", "open_loop", "--text", "Synthetic loop"])

    with vault_binding(tmp_path):
        add_state.main()
        with pytest.raises(SystemExit) as exit_info:
            add_state.main()

    assert exit_info.value.code == 1
    assert "already exists" in capsys.readouterr().err
    assert len(_files(tmp_path, "memory/state")) == 1
