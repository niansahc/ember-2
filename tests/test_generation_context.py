"""
tests/test_generation_context.py

Tests for the Phase A enrichment value builders that assemble the frozen
GenerationContext (ADR-042, issue #93 PR c).

These exercise _build_generation_context directly: given a request/body and the
normalization outputs (session_id, latest_user_message, completion_id), it must
resolve is_test / vault_enabled / skip_vault / project, ensure the session, and
memoize the query policy - the enrichment-resolved values the clarification
interceptor and the generation handler consume.
"""

from unittest.mock import patch
from types import SimpleNamespace

from src.api.openai_adapter import (
    _build_generation_context,
    _apply_confirmation,
    _apply_tasks,
    _apply_timers,
)
from src.api.pregeneration import GenerationContext, GenerationWork


class _FakeReq:
    def __init__(self, headers=None):
        self.headers = headers or {}


class _FakeBody:
    def __init__(self, vault_enabled=True, stream=False):
        self.vault_enabled = vault_enabled
        self.stream = stream


def test_build_generation_context_resolves_test_and_skip_vault():
    req = _FakeReq({"X-Test-Session": "true"})
    body = _FakeBody(vault_enabled=True, stream=True)
    with patch("src.api.openai_adapter._ensure_session") as ens, \
         patch("src.api.openai_adapter.get_session", return_value=None):
        ctx = _build_generation_context(
            req, body,
            session_id="sess_test_001",
            latest_user_message="hello world",
            completion_id="chatcmpl-abc",
        )
    assert ctx.is_test is True
    assert ctx.skip_vault is True          # is_test forces skip
    assert ctx.session_id == "sess_test_001"
    assert ctx.completion_id == "chatcmpl-abc"
    assert ctx.stream is True
    assert ctx.raw_user_message == "hello world"
    assert ctx.policy is not None          # memoized classify_query result
    # Session ensure ran once, gated on skip_vault.
    ens.assert_called_once_with("sess_test_001", "hello world", test=True)


def test_build_generation_context_resolves_project():
    req = _FakeReq({})
    body = _FakeBody()
    sess_rec = {"metadata": {"project_id": "proj_1"}}
    proj_rec = {"text": "Quarterly Planning"}
    with patch("src.api.openai_adapter._ensure_session"), \
         patch("src.api.openai_adapter.get_session", return_value=sess_rec), \
         patch("src.memory.project.get_project", return_value=proj_rec):
        ctx = _build_generation_context(
            req, body,
            session_id="sess_test_002",
            latest_user_message="hi",
            completion_id="chatcmpl-p",
        )
    assert ctx.project_id == "proj_1"
    assert ctx.project_name == "Quarterly Planning"


def test_build_generation_context_vault_disabled_sets_skip():
    req = _FakeReq({})
    body = _FakeBody(vault_enabled=False, stream=False)
    with patch("src.api.openai_adapter._ensure_session"), \
         patch("src.api.openai_adapter.get_session", return_value=None), \
         patch("src.core.preferences.read", return_value={"vault_toggle_enabled": True}):
        ctx = _build_generation_context(
            req, body,
            session_id="sess_test_003",
            latest_user_message="hi",
            completion_id="chatcmpl-v",
        )
    assert ctx.is_test is False
    assert ctx.vault_enabled is False      # per-request opt-out honored
    assert ctx.skip_vault is True          # vault disabled forces skip


# ---------------------------------------------------------------------------
# Phase B prep builders over GenerationWork (verbatim extractions).
# ---------------------------------------------------------------------------

def _gctx(is_test=False, skip_vault=False, session_id="sess_test_b", project_id=None):
    return GenerationContext(
        session_id=session_id, project_id=project_id, project_name=None,
        is_test=is_test, vault_enabled=True, skip_vault=skip_vault,
        completion_id="chatcmpl-b", stream=False, policy=None, raw_user_message="",
    )


def test_apply_confirmation_confirmed_web_search_overwrites_message():
    ctx = _gctx()
    work = GenerationWork(message="yes")
    result = {"confirmed": True, "action": "web_search", "query": "population of tokyo"}
    with patch("src.api.openai_adapter._check_pending_confirmation",
               return_value=(result, ["rec"])), \
         patch("src.tools.web_search.web_search", return_value=[{"title": "x"}]):
        _apply_confirmation(ctx, work)
    # Both the working message AND the clean snapshot become the original query.
    assert work.message == "population of tokyo"
    assert work.raw_message == "population of tokyo"
    assert work.confirmation_confirmed is True
    assert work.confirmation_web_items == [{"title": "x"}]
    assert work.pending_records == ["rec"]


def test_apply_confirmation_is_test_is_noop():
    ctx = _gctx(is_test=True)
    work = GenerationWork(message="yes")
    with patch("src.api.openai_adapter._check_pending_confirmation") as chk:
        _apply_confirmation(ctx, work)
    chk.assert_not_called()
    assert work.confirmation_confirmed is False
    assert work.pending_records == []


def test_apply_tasks_explicit_request_prefixes_message():
    ctx = _gctx()
    work = GenerationWork(message="make a task to call mom")
    created = SimpleNamespace(created=True, error=None)
    with patch("src.tasks.task_handler.detect_explicit_task_request",
               return_value=["call mom"]), \
         patch("src.tasks.task_handler.create_task", return_value=created), \
         patch("src.tasks.task_handler.check_pending_confirmation", return_value=None):
        _apply_tasks(ctx, work)
    assert work.message.startswith('[System: tasks created - "call mom"]')


def test_apply_timers_skip_vault_is_noop():
    ctx = _gctx(skip_vault=True)
    work = GenerationWork(message="start a timer for tea")
    _apply_timers(ctx, work)
    assert work.message == "start a timer for tea"


def test_apply_timers_start_prefixes_message():
    ctx = _gctx(skip_vault=False)
    work = GenerationWork(message="start a timer for tea")
    with patch("src.state.timer_service.detect_start_timer", return_value="tea"), \
         patch("src.state.timer_service.start_timer"):
        _apply_timers(ctx, work)
    assert work.message.startswith('[System: timer started for "tea"]')


def test_apply_timers_stop_note_uses_real_em_dash_at_runtime():
    # The builder emits U+2014 at runtime; the note text must stay
    # contain the actual em dash (U+2014), byte-identical to the pre-refactor note.
    ctx = _gctx(skip_vault=False)
    work = GenerationWork(message="stop the timer")
    active = [SimpleNamespace(text="tea", metadata={"started_at": "", "timer_id": "t1"})]
    with patch("src.state.timer_service.detect_start_timer", return_value=None), \
         patch("src.state.timer_service.detect_stop_timer", return_value=True), \
         patch("src.state.timer_service.detect_check_timer", return_value=False), \
         patch("src.state.timer_service.get_active_timers", return_value=active), \
         patch("src.state.timer_service.format_elapsed", return_value="1m"), \
         patch("src.state.timer_service.stop_timer"):
        _apply_timers(ctx, work)
    dash = chr(0x2014)
    assert dash in work.message
    assert work.message.startswith(f"[System: timer stopped {dash} was ")
