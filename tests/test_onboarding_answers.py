"""
tests/test_onboarding_answers.py

An onboarding answer is either saved or asked again, never dropped while the
step advances. write_memory returns None for text its filter refuses, and the
service used to ignore that.
"""

from __future__ import annotations

import logging

import pytest

from src.llm.aux_client import AUX_FORMAT_TIMEOUT_S
from src.memory.write_memory import should_skip_memory
from src.onboarding.service import OnboardingService
from src.onboarding.steps import ONBOARDING_STEPS
from src.state.state_service import StateService
from tests.conftest import _make_vault_tree, vault_records

SHORT_ANSWER = "My name is Sam."


@pytest.fixture
def vault(tmp_path, stub_write_embeddings):
    from src.core import config

    root = tmp_path / "vault"
    _make_vault_tree(root)
    config.set_vault_path_override(str(root), "test")
    return root


@pytest.fixture
def service(vault):
    svc = OnboardingService()
    svc._state = StateService(vault_path=vault)
    svc._write_progress(step=0, completed=[])
    return svc


def _answer(service, monkeypatch, formatted):
    monkeypatch.setattr(service, "_format_answer", lambda _answer, _category: formatted)
    return service.handle("synthetic answer")


def _current_step(service):
    return service._state.read_by_category("onboarding")[0].metadata["current_step"]


def test_a_short_answer_is_saved_and_the_step_advances(service, vault, monkeypatch):
    reply = _answer(service, monkeypatch, SHORT_ANSWER)

    assert [r["text"] for r in vault_records(vault, "profile")] == [SHORT_ANSWER]
    assert _current_step(service) == 1
    assert reply == ONBOARDING_STEPS[1].question


def test_control_the_length_floor_refuses_it_on_other_types():
    assert should_skip_memory(SHORT_ANSWER, memory_type="ingested") is True
    assert should_skip_memory(SHORT_ANSWER, memory_type="profile") is False


def test_refused_text_asks_the_same_step_again(service, vault, monkeypatch, caplog):
    with caplog.at_level(logging.WARNING):
        reply = _answer(service, monkeypatch, '{"not": "a profile sentence at all, sorry"}')

    assert vault_records(vault, "profile") == []
    assert _current_step(service) == 0
    assert reply == ONBOARDING_STEPS[0].question
    assert any("[ONBOARDING] Profile text refused" in r.getMessage() for r in caplog.records)


def test_format_timeout_allows_a_cold_cpu_call_within_the_client_limit():
    assert 150 <= AUX_FORMAT_TIMEOUT_S < 180
