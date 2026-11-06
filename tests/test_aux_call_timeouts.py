"""
tests/test_aux_call_timeouts.py

Every local auxiliary Ollama call on the request path goes through aux_chat
with its own client timeout. A bare ollama.chat has no timeout, so one wedged
call holds its thread forever (2026-11-01).

These sites fail open, so a broken call is invisible from the outside: each
site is run against a recording client instead, with ollama.chat recorded too.
The conftest shim is not in play here: the recording client replaces it.

Positive control: each site with its aux_chat replaced by the bare call it
used to make must fail the check, as must a site passing the wrong timeout.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock

import pytest

import src.api.main as main
import src.llm.aux_client as aux_client
import src.llm.intent_classifier as intent_classifier
import src.llm.vision_service as vision_service
from src.onboarding.service import OnboardingService
from tests.test_auxiliary_think import (
    _run_coaching_rewrite,
    _run_identity_check,
    _run_identity_rewrite,
    _run_review,
    _run_summarize,
)
from tests.test_thinking_budget_retry import _answer

_SRC = Path(__file__).resolve().parents[1] / "src"


def _run_stage3():
    intent_classifier._stage3_llm_call("synthetic query")


def _run_vision():
    vision_service.VisionService(model="synthetic-vision").analyze(["fake_b64_image"])


def _run_onboarding_format():
    OnboardingService.__new__(OnboardingService)._format_answer("Synthetic answer.", "work")


def _run_lodestone_inference():
    main._extract_lodestone_value("Synthetic answer.")


@dataclass(frozen=True)
class Site:
    run: Callable[[], None]
    module: str              # where the site's aux_chat name is bound
    timeout: Callable[[], float]
    reply: str = "A synthetic reply long enough to keep."


def _constant(name: str) -> Callable[[], float]:
    return lambda: getattr(aux_client, name)


_COACHING = "src.llm.coaching_filter"
_ADAPTER = "src.llm.adapter"

SITES = {
    "coaching_rewrite": Site(_run_coaching_rewrite, _COACHING, _constant("AUX_REWRITE_TIMEOUT_S")),
    "coaching_semantic_check": Site(_run_identity_check, _COACHING, _constant("AUX_CHECK_TIMEOUT_S"), "NO"),
    "coaching_identity_rewrite": Site(_run_identity_rewrite, _COACHING, _constant("AUX_REWRITE_TIMEOUT_S")),
    "stage3": Site(
        _run_stage3, "src.llm.intent_classifier", _constant("AUX_STAGE3_LOAD_TIMEOUT_S"),
        json.dumps({"label": "vault_answerable"}),
    ),
    "review": Site(_run_review, _ADAPTER, _constant("AUX_REVIEW_TIMEOUT_S"), "{}"),
    "buffer_summary": Site(_run_summarize, _ADAPTER, _constant("AUX_SUMMARY_TIMEOUT_S")),
    "vision": Site(_run_vision, "src.llm.vision_service", _constant("AUX_VISION_TIMEOUT_S")),
    "onboarding_format": Site(
        _run_onboarding_format, "src.onboarding.service", _constant("AUX_FORMAT_TIMEOUT_S")),
    "lodestone_inference": Site(
        _run_lodestone_inference, "src.api.main", _constant("AUX_LODESTONE_TIMEOUT_S")),
}


def _run_recorded(site: Site, monkeypatch) -> tuple[list[float], MagicMock, MagicMock]:
    """Run a site against a recording client. Returns (timeouts asked for,
    the client, the bare ollama.chat)."""
    timeouts: list[float] = []
    client = MagicMock()
    client.chat.return_value = _answer(site.reply)
    direct = MagicMock(return_value=_answer(site.reply))

    def recording_client(timeout_s):
        timeouts.append(timeout_s)
        return client

    monkeypatch.setattr(aux_client, "_client_for_timeout", recording_client)
    monkeypatch.setattr("ollama.chat", direct)

    site.run()
    return timeouts, client, direct


def _assert_site_uses_aux_chat(site: Site, monkeypatch) -> None:
    timeouts, client, direct = _run_recorded(site, monkeypatch)

    assert direct.call_count == 0, "called ollama.chat directly, with no timeout"
    assert timeouts == [site.timeout()]
    assert client.chat.call_count == 1


@pytest.fixture(autouse=True)
def vision_logs_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(vision_service, "_LOG_DIR", tmp_path / "vision")


@pytest.mark.parametrize("name", SITES)
def test_site_calls_aux_chat_with_its_timeout(name, monkeypatch):
    _assert_site_uses_aux_chat(SITES[name], monkeypatch)


def _bare_chat(*, tag, step, timeout_s, **chat_kwargs):
    """The pre-fix call: no client timeout, no timing line."""
    import ollama
    return ollama.chat(**chat_kwargs)


@pytest.mark.parametrize("name", SITES)
def test_control_a_site_reverted_to_ollama_chat_fails(name, monkeypatch):
    site = SITES[name]
    monkeypatch.setattr(f"{site.module}.aux_chat", _bare_chat)
    with pytest.raises(AssertionError, match="directly"):
        _assert_site_uses_aux_chat(site, monkeypatch)


def test_control_a_site_with_the_wrong_timeout_fails(monkeypatch):
    site = SITES["coaching_semantic_check"]
    real_aux_chat = aux_client.aux_chat

    def long_timeout(*, tag, step, timeout_s, **chat_kwargs):
        return real_aux_chat(tag=tag, step=step, timeout_s=timeout_s + 1, **chat_kwargs)

    monkeypatch.setattr(f"{site.module}.aux_chat", long_timeout)
    with pytest.raises(AssertionError):
        _assert_site_uses_aux_chat(site, monkeypatch)


# ---------------------------------------------------------------------------
# Budgets: a capped call fits its cap on the slowest supported hardware
# ---------------------------------------------------------------------------

# Sites with no num_predict; their budgets are stated in aux_client.py, not
# derived. Pinned so a cap added or removed is noticed here.
_UNCAPPED = {"stage3", "review", "buffer_summary", "onboarding_format"}


def _fits_the_floor(timeout_s: float, cap: int) -> bool:
    return timeout_s >= (cap / aux_client.SUPPORTED_MIN_TOKENS_PER_S
                         + aux_client.SUPPORTED_MAX_COLD_LOAD_S)


@pytest.mark.parametrize("name", SITES)
def test_a_capped_site_fits_its_cap_at_the_supported_floor(name, monkeypatch):
    timeouts, client, _ = _run_recorded(SITES[name], monkeypatch)
    cap = (client.chat.call_args.kwargs.get("options") or {}).get("num_predict")

    if name in _UNCAPPED:
        assert cap is None
        return
    assert cap is not None, f"{name} has no num_predict; list it in _UNCAPPED"
    assert _fits_the_floor(timeouts[0], cap), (name, timeouts[0], cap)


def test_control_a_500_token_cap_does_not_fit_30_seconds():
    assert not _fits_the_floor(30, 500)
    assert _fits_the_floor(45, 500)


# ---------------------------------------------------------------------------
# Source guard: a new call in these files has to join the table above
# ---------------------------------------------------------------------------

# Files holding request-path auxiliary calls -> how many sites each has.
_SITES_PER_FILE = {
    "llm/coaching_filter.py": 3,
    "llm/intent_classifier.py": 1,
    "llm/adapter.py": 2,
    "llm/vision_service.py": 1,
    "onboarding/service.py": 1,
    "api/main.py": 1,
}


def _count_calls(source: str) -> tuple[int, int]:
    """(aux_chat calls, ollama.chat calls) in a module's source."""
    aux = direct = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "aux_chat":
            aux += 1
        elif (isinstance(func, ast.Attribute) and func.attr == "chat"
              and isinstance(func.value, ast.Name) and func.value.id == "ollama"):
            direct += 1
    return aux, direct


@pytest.mark.parametrize("path, sites", _SITES_PER_FILE.items())
def test_every_call_in_a_request_path_file_is_a_listed_site(path, sites):
    source = (_SRC / path).read_text(encoding="utf-8")
    assert _count_calls(source) == (sites, 0), path


def test_the_table_and_the_file_counts_agree():
    assert len(SITES) == sum(_SITES_PER_FILE.values())


@pytest.mark.parametrize("call, counts", [
    pytest.param("ollama.chat(model='m')", (0, 1), id="control-bare-call"),
    pytest.param("aux_chat(tag='t', step='s', timeout_s=1)", (1, 0), id="aux-call"),
])
def test_call_counter(call, counts):
    assert _count_calls(f"def f():\n    {call}\n") == counts
