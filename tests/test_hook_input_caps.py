"""
tests/test_hook_input_caps.py

The PreToolUse guards fail closed and cap their input, matching the
ember-2-installer guards (.claude/hooks/hook-utils.cjs, env-guard.cjs).

A PreToolUse hook that times out, crashes or cannot start lets the tool call
run unless its settings entry says "onFailure": "block". So every guard entry
blocks on failure, and input that could make a guard slow enough to time out
is denied before it is checked:
  - a Bash or PowerShell command over MAX_COMMAND_CHARS (env_guard and
    secret_dump_guard, before parsing);
  - a brace pattern with more than MAX_LIVE_BRACES `{` or more than
    MAX_BRACE_WORDS alternatives (env_guard, whose expansion is exponential:
    `.e{a,b}` x 21 took 5.5 s before the cap, over the 5 s hook timeout).

Every deny below has a positive control: the same input at the cap is
allowed, so the denies come from the cap and not from the input.

All runs are hook subprocesses in a hermetic tmp project: no real vault and
no .env are read.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.conftest import hook_output, hook_payload, run_hook

REPO = Path(__file__).resolve().parents[1]
MAX_COMMAND_CHARS = 16384
MAX_BRACES = 256
SHELL_GUARDS = ("env_guard.py", "secret_dump_guard.py")


def _benign_command(length: int) -> str:
    """An echo of `length` characters that names no .env file or vault."""
    head = "echo "
    return head + "x" * (length - len(head))


def _decision(guard: str, payload: dict, cwd: Path) -> dict | None:
    return hook_output(run_hook(guard, payload, cwd=cwd))


# ---------------------------------------------------------------------------
# Over-length shell commands
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("guard", SHELL_GUARDS)
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_command_over_the_length_cap_is_denied(guard, tool, hermetic_hook_project):
    payload = hook_payload(tool, cwd=str(hermetic_hook_project),
                           command=_benign_command(MAX_COMMAND_CHARS + 1))
    out = _decision(guard, payload, hermetic_hook_project)
    assert out is not None and out["permissionDecision"] == "deny"
    assert str(MAX_COMMAND_CHARS) in out["permissionDecisionReason"]


@pytest.mark.parametrize("guard", SHELL_GUARDS)
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
def test_command_at_the_length_cap_is_allowed(guard, tool, hermetic_hook_project):
    """Positive control for the deny above."""
    payload = hook_payload(tool, cwd=str(hermetic_hook_project),
                           command=_benign_command(MAX_COMMAND_CHARS))
    assert _decision(guard, payload, hermetic_hook_project) is None


# ---------------------------------------------------------------------------
# Brace patterns (env_guard)
# ---------------------------------------------------------------------------

def _pairs(n: int) -> str:
    """A dot-name with n brace sets of two alternatives: 2**n words."""
    return ".e" + "{a,b}" * n


def _singles(n: int) -> str:
    """A dot-name with n one-alternative brace sets: n braces, one word."""
    return ".e" + "{a}" * n


def _brace_payloads(pattern: str, cwd: Path) -> list[dict]:
    """The pattern in each kind of field env_guard expands braces in. Shell
    commands are not among them: env_guard splits a command's words at
    `{`, `}` and `,`, so a shell word never holds a brace set."""
    return [
        hook_payload("Glob", cwd=str(cwd), pattern=pattern),
        hook_payload("Grep", cwd=str(cwd), pattern="x", glob=pattern),
        hook_payload("Read", cwd=str(cwd), file_path=str(cwd / pattern)),
    ]


@pytest.mark.parametrize("pattern", [_pairs(9), _singles(MAX_BRACES + 1)],
                         ids=["512-alternatives", "257-braces"])
def test_brace_pattern_over_the_cap_is_denied(pattern, hermetic_hook_project):
    for payload in _brace_payloads(pattern, hermetic_hook_project):
        out = _decision("env_guard.py", payload, hermetic_hook_project)
        assert out is not None and out["permissionDecision"] == "deny", payload["tool_name"]
        assert "brace" in out["permissionDecisionReason"]
        assert str(MAX_BRACES) in out["permissionDecisionReason"]


@pytest.mark.parametrize("pattern", [_pairs(8), _singles(MAX_BRACES)],
                         ids=["256-alternatives", "256-braces"])
def test_brace_pattern_at_the_cap_is_allowed(pattern, hermetic_hook_project):
    """Positive control for the deny above."""
    for payload in _brace_payloads(pattern, hermetic_hook_project):
        assert _decision("env_guard.py", payload, hermetic_hook_project) is None, payload["tool_name"]


def test_exponential_brace_pattern_is_denied_before_the_hook_timeout(hermetic_hook_project):
    """`.e{a,b}` x 21 has 21 braces, under the brace cap, and took 5.5 s to
    expand before the alternatives cap: past the 5 s hook timeout."""
    payload = hook_payload("Glob", cwd=str(hermetic_hook_project), pattern=_pairs(21))
    start = time.perf_counter()
    out = _decision("env_guard.py", payload, hermetic_hook_project)
    elapsed = time.perf_counter() - start
    assert out is not None and out["permissionDecision"] == "deny"
    assert elapsed < 3.0, f"took {elapsed:.2f} s"


def test_brace_limit_message_names_both_caps(hermetic_hook_project):
    payload = hook_payload("Glob", cwd=str(hermetic_hook_project), pattern=_pairs(9))
    reason = _decision("env_guard.py", payload, hermetic_hook_project)["permissionDecisionReason"]
    assert f"{MAX_BRACES} alternatives" in reason and f"{MAX_BRACES} braces" in reason


def test_brace_set_that_names_env_is_still_denied_as_env(hermetic_hook_project):
    """The caps do not change the ordinary .env rule."""
    payload = hook_payload("Glob", cwd=str(hermetic_hook_project), pattern=".env{,.local}")
    out = _decision("env_guard.py", payload, hermetic_hook_project)
    assert out is not None and ".env files" in out["permissionDecisionReason"]


# ---------------------------------------------------------------------------
# Normal input passes every guard
# ---------------------------------------------------------------------------

def _normal_calls(project: Path) -> list[dict]:
    src = project / "src"
    return [
        hook_payload("Read", cwd=str(project), file_path=str(src / "app.py")),
        hook_payload("Edit", cwd=str(project), file_path=str(src / "app.py"),
                     old_string="a", new_string="b"),
        hook_payload("Write", cwd=str(project), file_path=str(src / "new.py"), content="x = 1\n"),
        hook_payload("Grep", cwd=str(project), pattern="def main", path="src", glob="*.py"),
        hook_payload("Glob", cwd=str(project), pattern="src/**/*.{py,md}"),
        hook_payload("Bash", cwd=str(project), command="git status && pytest tests/ -q"),
        hook_payload("PowerShell", cwd=str(project), command="Get-ChildItem src | Select-Object Name"),
    ]


_GUARD_TOOLS = {
    "env_guard.py": {"Read", "Grep", "Glob", "Edit", "Write", "Bash", "PowerShell"},
    "vault_guard.py": {"Read", "Grep", "Glob", "Edit", "Write"},
    "secret_dump_guard.py": {"Bash", "PowerShell"},
}


@pytest.mark.parametrize("guard", sorted(_GUARD_TOOLS))
def test_normal_calls_pass_every_guard(guard, hermetic_hook_project):
    """The absence half of this file: ordinary calls are not denied. The
    denies above (over-length, over-brace) are its positive controls."""
    (hermetic_hook_project / "src").mkdir()
    for payload in _normal_calls(hermetic_hook_project):
        if payload["tool_name"] in _GUARD_TOOLS[guard]:
            assert _decision(guard, payload, hermetic_hook_project) is None, payload


# ---------------------------------------------------------------------------
# Hook failure blocks the call (settings.json)
# ---------------------------------------------------------------------------

def _command_hooks(event: str) -> dict[str, dict]:
    """The command hooks of `event` in .claude/settings.json, by script name."""
    settings = json.loads((REPO / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hooks = {}
    for entry in settings["hooks"][event]:
        for hook in entry["hooks"]:
            script = hook["command"].rsplit("/", 1)[-1].strip('"')
            hooks[script] = hook
    return hooks


@pytest.mark.parametrize("guard", sorted(_GUARD_TOOLS))
def test_guard_entry_blocks_on_hook_failure(guard):
    """A PreToolUse hook that times out, crashes or cannot start lets the
    call run unless its entry sets onFailure "block" (Claude Code 2.1.295+)."""
    assert _command_hooks("PreToolUse")[guard].get("onFailure") == "block"


@pytest.mark.parametrize("script", ["post_edit_test.py", "post_commit_eval.py"])
def test_post_hooks_do_not_block_on_failure(script):
    """Control: the test hooks keep the default, so the check above reads
    each entry rather than passing on a file-wide setting."""
    assert "onFailure" not in _command_hooks("PostToolUse")[script]
