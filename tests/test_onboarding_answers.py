"""
tests/test_onboarding_answers.py

An onboarding answer is either saved or asked again, never dropped while the
step advances. write_memory returns None for text its filter refuses, and the
service used to ignore that.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections import Counter

import pytest

from src.memory.write_memory import should_skip_memory
from src.onboarding.service import _TRANSITION, OnboardingService
from src.onboarding.steps import ONBOARDING_STEPS
from src.state.state_service import StateService
from tests.conftest import _make_vault_tree, run_in_threads, vault_records

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


REFUSED = '{"not": "a profile sentence at all, sorry"}'


def _messages(caplog):
    return [r.getMessage() for r in caplog.records]


def test_refused_formatting_falls_back_to_the_raw_answer(service, vault, monkeypatch, caplog):
    with caplog.at_level(logging.INFO, logger="ember.onboarding"):
        reply = _answer(service, monkeypatch, REFUSED)

    assert [r["text"] for r in vault_records(vault, "profile")] == ["synthetic answer"]
    assert _current_step(service) == 1
    assert reply == ONBOARDING_STEPS[1].question
    assert any("retrying with the raw answer" in m for m in _messages(caplog))


def test_an_answer_refused_twice_is_skipped_and_the_step_advances(
    service, vault, monkeypatch, caplog,
):
    """The control is the test above: the same path saves a record when the
    raw answer is accepted."""
    monkeypatch.setattr(service, "_format_answer", lambda _answer, _category: REFUSED)
    with caplog.at_level(logging.INFO, logger="ember.onboarding"):
        reply = service.handle(REFUSED)

    assert vault_records(vault, "profile") == []
    assert _current_step(service) == 1
    assert reply == ONBOARDING_STEPS[1].question
    progress = service._state.read_by_category("onboarding")[0].metadata
    assert ONBOARDING_STEPS[0].key not in progress["completed_steps"]
    assert any("skipped without saving" in m for m in _messages(caplog))


# --- Two sessions at once -------------------------------------------------
# Onboarding progress is one record for the install, and the turn lock is per
# session, so two tabs can answer at the same time. The service serializes
# them itself.


def _slow_format(service, monkeypatch):
    def slow(answer, _category):
        time.sleep(0.3)
        return f"I answered {answer}."
    monkeypatch.setattr(service, "_format_answer", slow)


def _records_per_step(vault) -> Counter:
    return Counter(r["metadata"]["onboarding_step"] for r in vault_records(vault, "profile"))


def _two_answers(service, monkeypatch, call):
    _slow_format(service, monkeypatch)
    results, errors = run_in_threads(lambda i: call(f"synthetic answer {i}"), 2)
    assert errors == []
    return results


def test_two_sessions_answering_at_once_save_one_answer_per_step(service, vault, monkeypatch):
    _two_answers(service, monkeypatch, service.handle)

    assert _records_per_step(vault) == Counter(
        {ONBOARDING_STEPS[0].key: 1, ONBOARDING_STEPS[1].key: 1})
    assert _current_step(service) == 2


def test_control_without_the_lock_both_save_the_same_step(service, vault, monkeypatch):
    monkeypatch.setattr(service, "_lock", contextlib.nullcontext())
    _two_answers(service, monkeypatch, service.handle)

    assert _records_per_step(vault)[ONBOARDING_STEPS[0].key] == 2


def _at_final_step(service):
    last = len(ONBOARDING_STEPS) - 1
    service._write_progress(step=last, completed=[s.key for s in ONBOARDING_STEPS[:last]])
    return ONBOARDING_STEPS[last].key


def test_a_turn_arriving_after_completion_is_not_handled(service, vault, monkeypatch):
    """Both turns saw onboarding active before either finished it. The second
    must not read the completion record as step 0; it gets None and runs as a
    normal turn."""
    final_key = _at_final_step(service)
    results = _two_answers(service, monkeypatch, service.handle)

    assert sorted(results, key=str) == [None, _TRANSITION]
    assert _records_per_step(vault) == Counter({final_key: 1})
    assert service.is_active() is False


def test_control_without_the_lock_both_turns_answer_the_final_step(service, vault, monkeypatch):
    monkeypatch.setattr(service, "_lock", contextlib.nullcontext())
    final_key = _at_final_step(service)
    results = _two_answers(service, monkeypatch, service.handle)

    assert None not in results
    assert _records_per_step(vault)[final_key] == 2
