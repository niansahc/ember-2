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
from src.llm.prompt_guardrail import OLLAMA_NUM_PREDICT
from src.safety.models import SafetyTriggerResult


def _bare_adapter():
    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "test-model"
    return adapter


def _answer(text="A plain answer."):
    return {"message": {"content": text}, "done_reason": "stop", "eval_count": 12}


def _scripted(*replies):
    """fake ollama.chat returning `replies` in order; records each call."""
    calls: list[dict] = []
    queue = list(replies)

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return queue.pop(0)

    return calls, fake_chat


def _turn(*replies, packet=None):
    """Run one generate_response_iter turn on a scripted ollama.chat;
    review not triggered. Returns (final reply, calls)."""
    adapter = _bare_adapter()
    adapter.policy_service = MagicMock()
    adapter.policy_service.evaluate_trigger.return_value = SafetyTriggerResult(
        triggered=False, triggered_by=[])
    adapter._maybe_compress_buffer = MagicMock()
    buf = MagicMock(question_suppressed=False)
    packet = packet or ContextPacket(user_message="That was a hard week.")
    calls, fake_chat = _scripted(*replies)
    with patch("ollama.chat", side_effect=fake_chat), \
            patch.object(adapter, "_get_num_ctx", return_value=8192), \
            patch.object(adapter, "_prepare_turn_prompt",
                         return_value=(buf, "system prompt", packet)):
        items = list(adapter.generate_response_iter(packet))
    return items[-1], calls


def test_setting_false_turns_thinking_off_on_a_turn(monkeypatch):
    monkeypatch.setenv("EMBER_GENERATION_THINK", "false")
    reply, calls = _turn(_answer())
    assert reply == "A plain answer."
    assert calls[0]["think"] is False


def test_unset_passes_no_think(monkeypatch):
    """Absence test; test_setting_false_turns_thinking_off_on_a_turn is its
    positive control."""
    monkeypatch.delenv("EMBER_GENERATION_THINK", raising=False)
    reply, calls = _turn(_answer())
    assert reply == "A plain answer."
    assert "think" not in calls[0]


# --- the search-failure arm ------------------------------------------------

def _failed_search_packet():
    return ContextPacket(user_message="q", web_items=[],
                         web_search_status="unavailable")


def test_search_failure_arm_unchanged_when_setting_unset(monkeypatch):
    monkeypatch.delenv("EMBER_GENERATION_THINK", raising=False)
    monkeypatch.setenv("EMBER_SEARCH_FAIL_THINK_OFF", "true")
    _, calls = _turn(_answer(), packet=_failed_search_packet())
    assert calls[0]["think"] is False


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

def _budget_spent():
    return {"message": {"content": "", "thinking": "reasoning " * 50},
            "done_reason": "length", "eval_count": OLLAMA_NUM_PREDICT}


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

def _answer_stream(*parts):
    chunks = [{"message": {"content": p}, "done": False} for p in parts]
    chunks.append({"message": {"content": ""}, "done": True,
                   "done_reason": "stop", "eval_count": 5})
    return iter(chunks)


def _stream_turn():
    """Run generate_response_stream on a scripted ollama.chat; returns calls."""
    adapter = _bare_adapter()
    adapter.policy_service = MagicMock()
    adapter.policy_service.evaluate_trigger.return_value = SafetyTriggerResult(
        triggered=False, triggered_by=[])
    adapter._maybe_compress_buffer = MagicMock()
    buf = MagicMock(question_suppressed=False)
    packet = ContextPacket(user_message="That was a hard week.")
    calls, fake_chat = _scripted(_answer_stream("A plain ", "answer."))
    with patch("ollama.chat", side_effect=fake_chat), \
            patch.object(adapter, "_get_num_ctx", return_value=8192), \
            patch.object(adapter, "_prepare_turn_prompt",
                         return_value=(buf, "system prompt", packet)):
        list(adapter.generate_response_stream(packet))
    return calls


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
    with patch("ollama.chat", side_effect=fake_chat), \
            patch.object(adapter, "_get_num_ctx", return_value=8192):
        assert adapter._chat("system", "user") == "A plain answer."
    assert "think" not in calls[0]


# --- the setting itself ---------------------------------------------------------

@pytest.fixture
def fresh_env_warnings():
    config_mod._INVALID_ENV_WARNED.clear()
    yield
    config_mod._INVALID_ENV_WARNED.clear()


@pytest.mark.parametrize("raw, expected, warns", [
    ("false", True, False),
    (" FALSE ", True, False),
    ("0", True, False),
    ("no", True, False),
    ("Off", True, False),
    ("", False, False),
    ("true", False, False),
    ("1", False, False),
    ("on", False, False),
    ("maybe", False, True),
])
def test_setting_parsing(monkeypatch, caplog, fresh_env_warnings, raw, expected, warns):
    """The "maybe" row is the positive control for the no-warning rows."""
    monkeypatch.setenv("EMBER_GENERATION_THINK", raw)
    with caplog.at_level("WARNING", logger="ember.config"):
        assert config_mod.get_generation_think_off() is expected
    assert ("EMBER_GENERATION_THINK" in caplog.text) is warns


def test_unset_setting_is_not_off(monkeypatch):
    monkeypatch.delenv("EMBER_GENERATION_THINK", raising=False)
    assert config_mod.get_generation_think_off() is False


# --- which mode a turn ran in, from the log ----------------------------------------

def test_log_says_thinking_off_from_the_setting(monkeypatch, caplog):
    monkeypatch.setenv("EMBER_GENERATION_THINK", "false")
    with caplog.at_level("INFO", logger="ember.llm"):
        _turn(_answer())
    assert "[GEN] turn thinking: off (EMBER_GENERATION_THINK=false)" in caplog.text
    assert "[GEN] think=False passed to ollama answer turn" in caplog.text
    assert "[SEARCH_FAIL]" not in caplog.text


def test_log_says_default_when_unset(monkeypatch, caplog):
    monkeypatch.delenv("EMBER_GENERATION_THINK", raising=False)
    with caplog.at_level("INFO", logger="ember.llm"):
        _turn(_answer())
    assert "[GEN] turn thinking: default" in caplog.text
    assert "[GEN] think=" not in caplog.text


def test_log_says_off_from_search_failure(monkeypatch, caplog):
    monkeypatch.delenv("EMBER_GENERATION_THINK", raising=False)
    monkeypatch.setenv("EMBER_SEARCH_FAIL_THINK_OFF", "true")
    with caplog.at_level("INFO", logger="ember.llm"):
        _turn(_answer(), packet=_failed_search_packet())
    assert "[GEN] turn thinking: off (search failure)" in caplog.text


# --- only the turn decision reads the setting ----------------------------------------

_REPO = Path(__file__).resolve().parent.parent


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
    adapter_source = (_REPO / "src" / "llm" / "adapter.py").read_text(encoding="utf-8")
    assert _readers(adapter_source) == {"turn_think"}


def test_second_reader_would_be_caught():
    """Positive control for the test above: a second caller is detected."""
    adapter_source = (_REPO / "src" / "llm" / "adapter.py").read_text(encoding="utf-8")
    extra = "\n\ndef _aux():\n    return get_generation_think_off()\n"
    assert _readers(adapter_source + extra) == {"turn_think", "_aux"}
