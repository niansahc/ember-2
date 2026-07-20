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

from src.api.openai_adapter import _build_generation_context


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
