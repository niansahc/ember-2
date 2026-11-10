"""
tests/test_generation_think_setting.py

The Thinking setting (ADR-049) on conversation-turn generation. The chat
handler resolves it once per request and passes thinking=... to the
adapter. Off sends think=False; on sends no think argument, so the model
uses its own default. The adapter never reads preferences, the retired env
vars have no effect, and session reflection's direct _chat call is not
affected. Everything is mocked: no Ollama.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.context.models import ContextPacket
from src.core import preferences
from src.llm.adapter import LLMAdapter
from src.safety.models import SafetyTriggerResult
from tests.test_thinking_budget_retry import (
    _answer,
    _answer_stream,
    _budget_spent,
    _ollama,
    _scripted,
)

_OFF_LOG = "[GEN] turn thinking: off (preference)"
_ON_LOG = "[GEN] turn thinking: on (model default)"


def _bare_adapter():
    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "test-model"
    return adapter


def _run(method, *replies, packet=None, **kwargs):
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
        items = list(getattr(adapter, method)(packet, **kwargs))
    return items, calls


def _turn(*replies, packet=None, **kwargs):
    """generate_response_iter turn. Returns (final reply, calls)."""
    items, calls = _run("generate_response_iter", *replies, packet=packet, **kwargs)
    return items[-1], calls


def _stream_turn(**kwargs):
    """generate_response_stream turn. Returns calls."""
    return _run("generate_response_stream",
                _answer_stream("A plain ", "answer."), **kwargs)[1]


# --- off and on -------------------------------------------------------------

def test_off_sends_think_false(caplog):
    """Positive control for test_on_sends_no_think."""
    with caplog.at_level("INFO", logger="ember.llm"):
        reply, calls = _turn(_answer(), thinking=False)
    assert reply == "A plain answer."
    assert calls[0]["think"] is False
    assert _OFF_LOG in caplog.text
    assert "[GEN] think=False passed to ollama answer turn" in caplog.text


def test_default_is_off():
    """A caller that passes nothing gets the shipped default: off."""
    _, calls = _turn(_answer())
    assert calls[0]["think"] is False


def test_on_sends_no_think(caplog):
    """Absence test; test_off_sends_think_false is its positive control."""
    with caplog.at_level("INFO", logger="ember.llm"):
        reply, calls = _turn(_answer(), thinking=True)
    assert reply == "A plain answer."
    assert "think" not in calls[0]
    assert _ON_LOG in caplog.text
    assert "[GEN] think=" not in caplog.text


def test_streamed_turn_off_sends_think_false():
    calls = _stream_turn(thinking=False)
    assert calls[0]["think"] is False and calls[0]["stream"] is True


def test_streamed_turn_on_sends_no_think():
    """Absence test; the test above is its positive control."""
    assert "think" not in _stream_turn(thinking=True)[0]


# --- the thinking-budget retry ----------------------------------------------

def test_budget_retry_fires_when_on():
    """Positive control for the test below."""
    reply, calls = _turn(_budget_spent(), _answer("That sounds like a lot."),
                         thinking=True)
    assert len(calls) == 2
    assert reply == "That sounds like a lot."


def test_no_budget_retry_when_off():
    """Absence test: a call already made with think=False is never retried."""
    reply, calls = _turn(_budget_spent(), _answer("That sounds like a lot."),
                         thinking=False)
    assert len(calls) == 1
    assert reply.startswith("I had trouble generating a response")


# --- the adapter never reads the preference ---------------------------------

def _stored_on(monkeypatch):
    """Make every preference read see generation_think=True, as if the user
    flipped the setting while the turn was running."""
    real_get = preferences.get

    def fake_get(key, default=None, vault_path=None):
        if key == "generation_think":
            return True
        return real_get(key, default, vault_path)

    monkeypatch.setattr(preferences, "get", fake_get)
    assert preferences.generation_thinking_enabled() is True


def test_mid_turn_flip_does_not_change_the_turn(monkeypatch):
    """Absence test: the stored value says on, the turn started off, and the
    call still sends think=False. Positive control:
    test_passed_value_changes_the_turn_under_the_same_store."""
    _stored_on(monkeypatch)
    _, calls = _turn(_answer(), thinking=False)
    assert calls[0]["think"] is False


def test_passed_value_changes_the_turn_under_the_same_store(monkeypatch):
    _stored_on(monkeypatch)
    _, calls = _turn(_answer(), thinking=True)
    assert "think" not in calls[0]


# --- retired env vars ----------------------------------------------------------

def test_retired_env_vars_do_not_turn_thinking_on(monkeypatch):
    """Absence test; test_retired_env_var_does_not_turn_thinking_off flips
    only the passed value and gets the other result."""
    monkeypatch.setenv("EMBER_GENERATION_THINK", "true")
    monkeypatch.setenv("EMBER_SEARCH_FAIL_THINK_OFF", "false")
    _, calls = _turn(_answer(), thinking=False)
    assert calls[0]["think"] is False


def test_retired_env_var_does_not_turn_thinking_off(monkeypatch):
    monkeypatch.setenv("EMBER_GENERATION_THINK", "false")
    monkeypatch.setenv("EMBER_SEARCH_FAIL_THINK_OFF", "true")
    _, calls = _turn(_answer(), thinking=True)
    assert "think" not in calls[0]


# --- search failures follow the setting ------------------------------------------

def _failed_search_packet():
    return ContextPacket(user_message="q", web_items=[],
                         web_search_status="unavailable")


def test_failed_search_turn_with_thinking_on_sends_no_think():
    """The search-failure arm is gone. Absence test; the test below is its
    positive control."""
    _, calls = _turn(_answer(), packet=_failed_search_packet(), thinking=True)
    assert "think" not in calls[0]


def test_failed_search_turn_with_thinking_off_sends_think_false():
    _, calls = _turn(_answer(), packet=_failed_search_packet(), thinking=False)
    assert calls[0]["think"] is False


# --- not a conversation turn --------------------------------------------------------

def test_direct_chat_call_is_not_affected():
    """Session reflection calls _chat directly. Absence test; its positive
    control is test_off_sends_think_false."""
    adapter = _bare_adapter()
    calls, fake_chat = _scripted(_answer())
    with _ollama(adapter, fake_chat):
        assert adapter._chat("system", "user") == "A plain answer."
    assert "think" not in calls[0]


# --- one reader, one decision point ---------------------------------------------------

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
_KEY = "generation_think"
_RESOLVER = "generation_thinking_enabled"


def _src_files() -> dict[str, str]:
    return {
        p.relative_to(_REPO).as_posix(): p.read_text(encoding="utf-8")
        for p in _SRC.rglob("*.py")
    }


def _files_with_key(files: dict[str, str]) -> set[str]:
    """Files holding the preference key as a string constant."""
    return {
        name for name, source in files.items()
        if any(isinstance(n, ast.Constant) and n.value == _KEY
               for n in ast.walk(ast.parse(source)))
    }


def _resolver_callers(files: dict[str, str]) -> set[str]:
    """Files that call generation_thinking_enabled, by name or attribute."""
    def calls_it(node):
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        return getattr(func, "id", None) == _RESOLVER or getattr(func, "attr", None) == _RESOLVER
    return {
        name for name, source in files.items()
        if any(calls_it(n) for n in ast.walk(ast.parse(source)))
    }


def test_only_preferences_and_the_settings_endpoint_name_the_key():
    """The key is read in preferences.py (default + resolver) and written by
    the settings endpoint in main.py. Nothing else touches it."""
    assert _files_with_key(_src_files()) == {
        "src/core/preferences.py", "src/api/main.py"}


def test_resolver_called_only_by_the_chat_handler_and_settings_endpoint():
    assert _resolver_callers(_src_files()) == {
        "src/api/openai_adapter.py", "src/api/main.py"}


def test_a_second_reader_would_be_caught():
    """Positive control for the two tests above."""
    files = _src_files()
    files["src/llm/extra.py"] = (
        "from src.core import preferences\n\n"
        "def _aux():\n"
        "    return preferences.get('generation_think')\n\n"
        "def _aux2():\n"
        "    return preferences.generation_thinking_enabled()\n"
    )
    assert "src/llm/extra.py" in _files_with_key(files)
    assert "src/llm/extra.py" in _resolver_callers(files)


_RETIRED = ("EMBER_GENERATION_THINK", "EMBER_SEARCH_FAIL_THINK_OFF",
            "get_generation_think_off", "get_search_failure_think_off",
            "search_failure_think")


def _retired_hits(files: dict[str, str]) -> set[str]:
    pattern = re.compile(r"\b(" + "|".join(_RETIRED) + r")\b")
    return {name for name, source in files.items() if pattern.search(source)}


def test_retired_names_are_gone_from_src():
    assert _retired_hits(_src_files()) == set()


def test_retired_name_would_be_caught():
    """Positive control for the test above."""
    files = {"src/x.py": 'os.getenv("EMBER_GENERATION_THINK")\n'}
    assert _retired_hits(files) == {"src/x.py"}
