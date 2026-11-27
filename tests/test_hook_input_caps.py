"""
tests/test_hook_input_caps.py

The PreToolUse guards fail closed and cap their input, matching the
ember-2-installer guards. The rationale lives with the caps in
.claude/hooks/hook_utils.py (MAX_COMMAND_CHARS, MAX_LIVE_BRACES,
MAX_BRACE_WORDS, run_hook).

Each cap is tested on both sides of its boundary, so every deny has its
allow control in the same test. Most cases drive each guard's main()
in-process; the hook-process tests run the real scripts.

Hermetic tmp project: no real vault and no .env are read.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT, hook_output, hook_payload, load_hook, run_hook

hook_utils = load_hook("hook_utils.py")
MAX_COMMAND_CHARS = hook_utils.MAX_COMMAND_CHARS
MAX_LIVE_BRACES = hook_utils.MAX_LIVE_BRACES
MAX_BRACE_WORDS = hook_utils.MAX_BRACE_WORDS
GUARDS = {name: load_hook(name) for name in ("env_guard.py", "vault_guard.py", "secret_dump_guard.py")}
SHELL_GUARDS = ("env_guard.py", "secret_dump_guard.py")


def test_caps_match_the_installer_guards():
    """The values are pinned to ember-2-installer/.claude/hooks/hook-utils.cjs."""
    assert (MAX_COMMAND_CHARS, MAX_LIVE_BRACES, MAX_BRACE_WORDS) == (16384, 256, 256)


@pytest.fixture
def decide(monkeypatch, capsys):
    """Run a guard's main() in-process on `payload`; its decision or None."""
    def run(guard: str, payload: dict) -> dict | None:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        GUARDS[guard].main()
        out = capsys.readouterr().out.strip()
        return json.loads(out)["hookSpecificOutput"] if out else None
    return run


def _benign_command(length: int) -> str:
    """An echo of `length` characters that names no .env file or vault."""
    return "echo " + "x" * (length - len("echo "))


# ---------------------------------------------------------------------------
# Over-length shell commands
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("guard", SHELL_GUARDS)
@pytest.mark.parametrize("tool", ["Bash", "PowerShell"])
@pytest.mark.parametrize("length, denied", [(MAX_COMMAND_CHARS, False), (MAX_COMMAND_CHARS + 1, True)])
def test_command_length_cap(guard, tool, length, denied, decide, hermetic_hook_project):
    payload = hook_payload(tool, cwd=str(hermetic_hook_project), command=_benign_command(length))
    out = decide(guard, payload)
    assert (out is not None) == denied
    if denied:
        assert f"over the {MAX_COMMAND_CHARS} limit" in out["permissionDecisionReason"]


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


_WORD_BITS = MAX_BRACE_WORDS.bit_length() - 1  # 2**_WORD_BITS == MAX_BRACE_WORDS


@pytest.mark.parametrize("pattern, denied", [
    (_pairs(_WORD_BITS), False),
    (_pairs(_WORD_BITS + 1), True),
    (_singles(MAX_LIVE_BRACES), False),
    (_singles(MAX_LIVE_BRACES + 1), True),
], ids=["words-at-cap", "words-over-cap", "braces-at-cap", "braces-over-cap"])
def test_brace_caps(pattern, denied, decide, hermetic_hook_project):
    for payload in _brace_payloads(pattern, hermetic_hook_project):
        out = decide("env_guard.py", payload)
        assert (out is not None) == denied, payload["tool_name"]
        if denied:
            reason = out["permissionDecisionReason"]
            assert f"{MAX_BRACE_WORDS} alternatives" in reason
            assert f"{MAX_LIVE_BRACES} braces" in reason


def test_brace_set_that_names_env_is_still_denied_as_env(decide, hermetic_hook_project):
    """The caps do not change the ordinary .env rule (Glob; Grep is in
    test_env_guard.py)."""
    out = decide("env_guard.py", hook_payload("Glob", cwd=str(hermetic_hook_project), pattern=".env{,.local}"))
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
def test_normal_calls_pass_every_guard(guard, decide, hermetic_hook_project):
    """The cap denies above are this test's positive controls."""
    (hermetic_hook_project / "src").mkdir()
    for payload in _normal_calls(hermetic_hook_project):
        if payload["tool_name"] in _GUARD_TOOLS[guard]:
            assert decide(guard, payload) is None, payload


# ---------------------------------------------------------------------------
# The real hook processes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("guard", SHELL_GUARDS)
def test_hook_process_denies_an_over_length_command(guard, hermetic_hook_project):
    payload = hook_payload("Bash", cwd=str(hermetic_hook_project),
                           command=_benign_command(MAX_COMMAND_CHARS + 1))
    result = run_hook(guard, payload, cwd=hermetic_hook_project)
    out = hook_output(result)
    assert result.returncode == 0
    assert out is not None and f"over the {MAX_COMMAND_CHARS} limit" in out["permissionDecisionReason"]


def test_hook_process_denies_an_exponential_brace_pattern_before_the_timeout(hermetic_hook_project):
    """`.e{a,b}` x 21 is under the brace cap and took 5.5 s to expand before
    the alternatives cap, past the 5 s hook timeout."""
    payload = hook_payload("Glob", cwd=str(hermetic_hook_project), pattern=_pairs(21))
    start = time.perf_counter()
    out = hook_output(run_hook("env_guard.py", payload, cwd=hermetic_hook_project))
    elapsed = time.perf_counter() - start
    assert out is not None and out["permissionDecision"] == "deny"
    assert elapsed < 3.0, f"took {elapsed:.2f} s"


# ---------------------------------------------------------------------------
# Hook failure blocks the call (settings.json)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("guard", sorted(_GUARD_TOOLS))
def test_guard_entry_blocks_on_hook_failure(guard):
    """A PreToolUse hook that times out, crashes or cannot start lets the
    call run unless its entry sets onFailure "block" (Claude Code 2.1.295+)."""
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    entries = [
        hook for entry in settings["hooks"]["PreToolUse"] for hook in entry["hooks"]
        if hook["command"].rstrip('"').endswith(f"/{guard}")
    ]
    assert len(entries) == 1 and entries[0].get("onFailure") == "block"
