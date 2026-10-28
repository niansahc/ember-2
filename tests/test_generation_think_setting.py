"""
tests/test_generation_think_setting.py

EMBER_GENERATION_THINK=false turns thinking off on conversation-turn
generation only. Unset leaves the call exactly as before: no think kwarg.
Everything is mocked: no Ollama.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.context.models import ContextPacket
from src.core import config as config_mod
from src.llm.adapter import LLMAdapter
from src.safety.models import SafetyTriggerResult
from tests.test_thinking_budget_retry import (
    _answer,
    _answer_stream,
    _budget_spent,
    _ollama,
    _scripted,
)


def _bare_adapter():
    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "test-model"
    return adapter


def _run(method, *replies, packet=None):
    """Run one turn through adapter.<method> on a scripted ollama.chat;
    review not triggered. Returns (items yielded, calls)."""
    adapter = _bare_adapter()
    adapter.policy_service = MagicMock()
    adapter.policy_service.evaluate_trigger.return_value = SafetyTriggerResult(
        triggered=False, triggered_by=[])
    adapter._maybe_compress_buffer = MagicMock()
    buf = MagicMock(question_suppressed=False)
    packet = packet or ContextPacket(user_message="That was a hard week.")
    calls, fake_chat = _scripted(*replies)
    with _ollama(adapter, fake_chat), \
            patch.object(adapter, "_prepare_turn_prompt",
                         return_value=(buf, "system prompt", packet)):
        items = list(getattr(adapter, method)(packet))
    return items, calls


def _turn(*replies, packet=None):
    """generate_response_iter turn. Returns (final reply, calls)."""
    items, calls = _run("generate_response_iter", *replies, packet=packet)
    return items[-1], calls


def test_setting_false_turns_thinking_off_on_a_turn(monkeypatch, caplog):
    monkeypatch.setenv("EMBER_GENERATION_THINK", "false")
    with caplog.at_level("INFO", logger="ember.llm"):
        reply, calls = _turn(_answer())
    assert reply == "A plain answer."
    assert calls[0]["think"] is False
    assert "[GEN] turn thinking: off (EMBER_GENERATION_THINK=false)" in caplog.text
    assert "[GEN] think=False passed to ollama answer turn" in caplog.text
    assert "[SEARCH_FAIL]" not in caplog.text


def test_unset_passes_no_think(monkeypatch, caplog):
    """Absence test; test_setting_false_turns_thinking_off_on_a_turn is its
    positive control."""
    monkeypatch.delenv("EMBER_GENERATION_THINK", raising=False)
    with caplog.at_level("INFO", logger="ember.llm"):
        reply, calls = _turn(_answer())
    assert reply == "A plain answer."
    assert "think" not in calls[0]
    assert "[GEN] turn thinking: default" in caplog.text
    assert "[GEN] think=" not in caplog.text


# --- the search-failure arm ------------------------------------------------

def _failed_search_packet():
    return ContextPacket(user_message="q", web_items=[],
                         web_search_status="unavailable")


def test_search_failure_arm_unchanged_when_setting_unset(monkeypatch, caplog):
    monkeypatch.delenv("EMBER_GENERATION_THINK", raising=False)
    monkeypatch.setenv("EMBER_SEARCH_FAIL_THINK_OFF", "true")
    with caplog.at_level("INFO", logger="ember.llm"):
        _, calls = _turn(_answer(), packet=_failed_search_packet())
    assert calls[0]["think"] is False
    assert "[GEN] turn thinking: off (search failure)" in caplog.text


def test_search_failure_arm_off_leaves_failed_search_turn_alone(monkeypatch):
    """Absence test; the test above is its positive control."""
    monkeypatch.delenv("EMBER_GENERATION_THINK", raising=False)
    monkeypatch.delenv("EMBER_SEARCH_FAIL_THINK_OFF", raising=False)
    _, calls = _turn(_answer(), packet=_failed_search_packet())
    assert "think" not in calls[0]


def test_setting_false_turns_thinking_off_on_a_successful_search_turn(monkeypatch):
    monkeypatch.setenv("EMBER_GENERATION_THINK", "false")
    hits = [{"title": "T", "url": "https://example.com/a", "snippet": "s"}]
    packet = ContextPacket(user_message="q", web_items=hits, web_search_status="ok")
    _, calls = _turn(_answer(), packet=packet)
    assert calls[0]["think"] is False


# --- the thinking-budget retry ----------------------------------------------

def test_no_budget_retry_when_thinking_is_off(monkeypatch):
    monkeypatch.setenv("EMBER_GENERATION_THINK", "false")
    reply, calls = _turn(_budget_spent(), _answer("That sounds like a lot."))
    assert len(calls) == 1
    assert reply.startswith("I had trouble generating a response")


def test_budget_retry_fires_when_setting_unset(monkeypatch):
    """Positive control for the test above: same script, setting unset."""
    monkeypatch.delenv("EMBER_GENERATION_THINK", raising=False)
    reply, calls = _turn(_budget_spent(), _answer("That sounds like a lot."))
    assert len(calls) == 2
    assert reply == "That sounds like a lot."


# --- streaming turn -----------------------------------------------------------

def _stream_turn():
    """generate_response_stream turn. Returns calls."""
    return _run("generate_response_stream", _answer_stream("A plain ", "answer."))[1]


def test_setting_false_turns_thinking_off_on_a_streamed_turn(monkeypatch):
    monkeypatch.setenv("EMBER_GENERATION_THINK", "false")
    calls = _stream_turn()
    assert calls[0]["think"] is False and calls[0]["stream"] is True


def test_streamed_turn_unset_passes_no_think(monkeypatch):
    """Absence test; the test above is its positive control."""
    monkeypatch.delenv("EMBER_GENERATION_THINK", raising=False)
    assert "think" not in _stream_turn()[0]


# --- not a conversation turn ---------------------------------------------------

def test_direct_chat_call_ignores_the_setting(monkeypatch):
    """Session reflection calls _chat directly. Absence test; its positive
    control is test_setting_false_turns_thinking_off_on_a_turn."""
    monkeypatch.setenv("EMBER_GENERATION_THINK", "false")
    adapter = _bare_adapter()
    calls, fake_chat = _scripted(_answer())
    with _ollama(adapter, fake_chat):
        assert adapter._chat("system", "user") == "A plain answer."
    assert "think" not in calls[0]


# --- the setting itself ---------------------------------------------------------

@pytest.mark.parametrize("raw, expected, warns", [
    ("false", True, False),
    (" FALSE ", True, False),
    ("0", True, False),
    ("no", True, False),
    ("Off", True, False),
    (None, False, False),
    ("", False, False),
    ("true", False, False),
    ("1", False, False),
    ("on", False, False),
    ("maybe", False, True),
])
def test_setting_parsing(monkeypatch, caplog, fresh_env_warnings, raw, expected, warns):
    """None is unset. The "maybe" row is the positive control for the
    no-warning rows."""
    if raw is None:
        monkeypatch.delenv("EMBER_GENERATION_THINK", raising=False)
    else:
        monkeypatch.setenv("EMBER_GENERATION_THINK", raw)
    with caplog.at_level("WARNING", logger="ember.config"):
        assert config_mod.get_generation_think_off() is expected
    assert ("EMBER_GENERATION_THINK" in caplog.text) is warns


# --- only the turn decision reads the setting ----------------------------------------

_REPO = Path(__file__).resolve().parent.parent
_ADAPTER = _REPO / "src" / "llm" / "adapter.py"


def _readers(source: str) -> set[str]:
    """Names of the functions in `source` that call get_generation_think_off."""
    return {
        fn.name for fn in ast.walk(ast.parse(source))
        if isinstance(fn, ast.FunctionDef)
        and any(isinstance(n, ast.Call) and getattr(n.func, "id", None) == "get_generation_think_off"
                for n in ast.walk(fn))
    }


def test_only_turn_think_reads_the_setting():
    outside = [
        p.relative_to(_REPO).as_posix() for p in (_REPO / "src").rglob("*.py")
        if "get_generation_think_off" in p.read_text(encoding="utf-8")
    ]
    assert sorted(outside) == ["src/core/config.py", "src/llm/adapter.py"]
    assert _readers(_ADAPTER.read_text(encoding="utf-8")) == {"turn_think"}


def test_second_reader_would_be_caught():
    """Positive control for the test above: a second caller is detected."""
    extra = "\n\ndef _aux():\n    return get_generation_think_off()\n"
    assert _readers(_ADAPTER.read_text(encoding="utf-8") + extra) == {"turn_think", "_aux"}
