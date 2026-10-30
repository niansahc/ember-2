"""
tests/test_auxiliary_think.py

Which local auxiliary ollama.chat calls run with thinking off.

qwen3 thinks unless the request carries a top-level think=False, and the
thinking tokens count against num_predict. Ollama ignores think inside
options (the Stage 3 defect). The split below was measured on synthetic
inputs (latency, empty replies, output validity):

  think=False  coaching_filter._check_semantic_identity_collapse,
               coaching_filter._rewrite_identity_collapse,
               LLMAdapter._summarize_with_plain_prompt,
               LLMAdapter._call_model_with_prompt,
               generate_reflection._llm_synthesize,
               StateExtractor._do_extract
  thinking on  coaching_filter._rewrite,
               session_reflection.generate_session_reflection

Every ollama.chat call is patched; nothing reaches the Ollama server.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

import src.llm.coaching_filter as coaching_filter
import src.reflection.generate_reflection as generate_reflection
import src.reflection.session_reflection as session_reflection
from src.state.state_extractor import StateExtractor
from tests.test_thinking_budget_retry import _answer, _bare_adapter, _scripted

_SRC = Path(__file__).resolve().parents[1] / "src"


# ---------------------------------------------------------------------------
# Kwarg checks shared by the per-site tests and their controls
# ---------------------------------------------------------------------------


def _think_off(kwargs: dict) -> bool:
    """True when the call turns thinking off where Ollama reads it."""
    return kwargs.get("think") is False and "think" not in (kwargs.get("options") or {})


def _no_think(kwargs: dict) -> bool:
    """True when the call leaves thinking at the model default."""
    return "think" not in kwargs and "think" not in (kwargs.get("options") or {})


_NON_DELETABLE = [{"pattern": "therapeutic_mid", "match": "your feelings are valid", "position": "body", "deletable": False}]
_RECORDS = [
    {"source": "journal", "timestamp": "2026-01-02T10-00-00", "text": "Synthetic journal entry about a garden."},
    {"source": "conversation", "timestamp": "2026-01-20T10-00-00", "text": "Synthetic conversation about a work plan."},
]
_TEMPLATE = "{record_count} {window_start} {window_end} {source_types}\n{records}"
_TURNS = [
    {"user": "Synthetic question one.", "assistant": "Synthetic answer one."},
    {"user": "Synthetic question two.", "assistant": "Synthetic answer two."},
    {"user": "Synthetic question three.", "assistant": "Synthetic answer three."},
]
_STATE_MESSAGE = "I need to finish the synthetic migration script by Friday and I am blocked on credentials."


def _run_identity_check():
    coaching_filter._check_semantic_identity_collapse("Synthetic draft response.")


def _run_identity_rewrite():
    coaching_filter._rewrite_identity_collapse("Synthetic draft response.")


def _run_summarize():
    _bare_adapter()._summarize_with_plain_prompt("Summarize: synthetic turns.")


def _run_review():
    _bare_adapter()._call_model_with_prompt("Review: synthetic draft.")


def _run_monthly_synthesis():
    generate_reflection._llm_synthesize(_RECORDS, _TEMPLATE, "journal, conversation")


def _run_state_extract():
    StateExtractor(model="qwen3:8b")._do_extract(_STATE_MESSAGE, "Okay.")


def _run_coaching_rewrite():
    coaching_filter._rewrite("Your feelings are valid. Synthetic body text.", _NON_DELETABLE)


def _run_session_reflection():
    with patch.object(session_reflection, "write_memory"):
        session_reflection.generate_session_reflection(_TURNS, session_id=None, model="qwen3:8b")


# (site runner, patch target, canned reply)
_THINK_OFF_SITES = [
    pytest.param(_run_identity_check, "ollama.chat", "NO", id="identity_check"),
    pytest.param(_run_identity_rewrite, "ollama.chat", "Synthetic rewrite.", id="identity_rewrite"),
    pytest.param(_run_summarize, "src.llm.adapter.ollama.chat", "Synthetic summary.", id="buffer_summary"),
    pytest.param(_run_review, "src.llm.adapter.ollama.chat", "{}", id="constitutional_review"),
    pytest.param(_run_monthly_synthesis, "ollama.chat", "Synthetic synthesis.", id="monthly_synthesis"),
    pytest.param(_run_state_extract, "src.state.state_extractor.ollama.chat", '{"extractions": []}', id="state_extract"),
]

_THINKING_SITES = [
    pytest.param(_run_coaching_rewrite, "ollama.chat", "Synthetic body text.", id="coaching_rewrite"),
    pytest.param(_run_session_reflection, "src.reflection.session_reflection.ollama.chat", "Synthetic reflection.", id="session_reflection"),
]


@pytest.mark.parametrize("run, target, reply", _THINK_OFF_SITES)
def test_site_sends_top_level_think_false(run, target, reply):
    calls, fake_chat = _scripted(_answer(reply))
    with patch(target, side_effect=fake_chat):
        run()
    assert len(calls) == 1, "the site never called ollama.chat"
    assert _think_off(calls[0]), calls[0]


@pytest.mark.parametrize("run, target, reply", _THINKING_SITES)
def test_site_leaves_thinking_on(run, target, reply):
    calls, fake_chat = _scripted(_answer(reply))
    with patch(target, side_effect=fake_chat):
        run()
    assert len(calls) == 1, "the site never called ollama.chat"
    assert _no_think(calls[0]), calls[0]


# Positive controls: the checks above must fail on the kwargs they guard against.


@pytest.mark.parametrize("check, kwargs", [
    pytest.param(_think_off, {"options": {"think": False}}, id="off-think-inside-options"),
    pytest.param(_think_off, {"options": {"temperature": 0}}, id="off-missing-think"),
    pytest.param(_think_off, {"think": False, "options": {"think": False}}, id="off-options-think-beside-top-level"),
    pytest.param(_no_think, {"think": False, "options": {}}, id="unset-top-level-think"),
    pytest.param(_no_think, {"options": {"think": True}}, id="unset-think-inside-options"),
])
def test_control_kwarg_checks_reject(check, kwargs):
    assert not check(kwargs)


# ---------------------------------------------------------------------------
# Identity check behaviour: the defect, modelled
# ---------------------------------------------------------------------------


def _ollama_like_chat(*args, **kwargs):
    """Answers YES only with thinking off; with thinking on, the 10-token
    budget goes to thinking and the content is empty (observed on qwen3:8b)."""
    if kwargs.get("think") is False:
        return {"message": {"content": "YES"}}
    return {"message": {"content": ""}}


def test_identity_check_can_return_yes():
    with patch("ollama.chat", side_effect=_ollama_like_chat):
        assert coaching_filter._check_semantic_identity_collapse("Synthetic draft.") is True


def test_control_identity_check_is_always_no_while_thinking():
    """The same fake, with think stripped from the call, reproduces the
    defect: the check can never say YES."""

    def thinking_chat(*args, **kwargs):
        kwargs.pop("think", None)
        return _ollama_like_chat(*args, **kwargs)

    with patch("ollama.chat", side_effect=thinking_chat):
        assert coaching_filter._check_semantic_identity_collapse("Synthetic draft.") is False


# ---------------------------------------------------------------------------
# Source guard: a new ollama.chat call has to decide about thinking
# ---------------------------------------------------------------------------

# (path under src/, enclosing function) -> why the call sends no think kwarg
_THINK_UNSET_ALLOWED = {
    ("llm/coaching_filter.py", "_rewrite"): "measured: thinking removes more coaching phrases",
    ("reflection/session_reflection.py", "generate_session_reflection"): "measured: no gain, background, no budget",
    ("llm/vision_service.py", "analyze"): "vision model, not an auxiliary text call",
    ("onboarding/service.py", "_format_answer"): "not yet measured",
}


def _unset_think_calls(source: str, path: str) -> list[tuple[str, str, int]]:
    """(path, function, line) for each ollama.chat call that does not pass a
    top-level think kwarg, or that puts think inside an options dict."""
    found = []

    def visit(node, func_name):
        for child in ast.iter_child_nodes(node):
            name = child.name if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else func_name
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "chat"
                and isinstance(child.func.value, ast.Name)
                and child.func.value.id == "ollama"
            ):
                keywords = {k.arg: k.value for k in child.keywords}
                options = keywords.get("options")
                think_in_options = isinstance(options, ast.Dict) and any(
                    isinstance(k, ast.Constant) and k.value == "think" for k in options.keys
                )
                if "think" not in keywords or think_in_options:
                    found.append((path, func_name, child.lineno))
            visit(child, name)

    visit(ast.parse(source), "<module>")
    return found


def _src_violations() -> list[tuple[str, str, int]]:
    violations = []
    for file in sorted(_SRC.rglob("*.py")):
        rel = file.relative_to(_SRC).as_posix()
        for path, func, line in _unset_think_calls(file.read_text(encoding="utf-8"), rel):
            if (path, func) not in _THINK_UNSET_ALLOWED:
                violations.append((path, func, line))
    return violations


def test_every_ollama_chat_call_decides_about_thinking():
    assert _src_violations() == []


def test_allowlisted_functions_exist():
    """An allowlist entry for a renamed function would hide nothing and
    rot silently."""
    for path, func in _THINK_UNSET_ALLOWED:
        tree = ast.parse((_SRC / path).read_text(encoding="utf-8"))
        names = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert func in names, (path, func)


@pytest.mark.parametrize("call, flagged", [
    pytest.param("ollama.chat(model='m', messages=[], options={'temperature': 0})", True, id="control-bare-call"),
    pytest.param("ollama.chat(model='m', messages=[], options={'think': False})", True, id="control-think-inside-options"),
    pytest.param("ollama.chat(model='m', options={'think': False}, think=False)", True, id="control-options-think-beside-top-level"),
    pytest.param("ollama.chat(model='m', messages=[], options={}, think=False)", False, id="top-level-think"),
])
def test_scanner(call, flagged):
    source = f"import ollama\ndef f():\n    {call}\n"
    assert _unset_think_calls(source, "x.py") == ([("x.py", "f", 3)] if flagged else [])
