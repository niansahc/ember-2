"""
tests/test_hook_input_caps.py

The PreToolUse guards fail closed and bound their work, matching the
ember-2-installer guards. The rationale lives with the caps in
.claude/hooks/hook_utils.py (MAX_COMMAND_CHARS, MAX_PATH_CHARS,
MAX_LIVE_BRACES, MAX_BRACE_WORDS, run_hook).

Each cap is tested on both sides of its boundary, so every deny has its
allow control in the same test. Most cases drive each guard's main()
in-process; the hook-process tests run the real scripts.

Hermetic tmp project: no real vault and no .env are read.
"""

from __future__ import annotations

import io
import json
import random
import sys
import time
import tracemalloc
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT, hook_output, hook_payload, load_hook, run_hook

GUARDS = {name: load_hook(name) for name in ("env_guard.py", "vault_guard.py", "secret_dump_guard.py")}
# The hook_utils module the guards imported, not a second copy.
hook_utils = sys.modules["hook_utils"]
MAX_COMMAND_CHARS = hook_utils.MAX_COMMAND_CHARS
MAX_PATH_CHARS = hook_utils.MAX_PATH_CHARS
MAX_LIVE_BRACES = hook_utils.MAX_LIVE_BRACES
MAX_BRACE_WORDS = hook_utils.MAX_BRACE_WORDS
SHELL_GUARDS = ("env_guard.py", "secret_dump_guard.py")


def test_caps_keep_their_pinned_values():
    """Pinned values. The command and brace caps were copied from the
    ember-2-installer guards (that repo's .claude/hooks/hook-utils.cjs); a
    change on either side is a deliberate edit to both."""
    assert (MAX_COMMAND_CHARS, MAX_LIVE_BRACES, MAX_BRACE_WORDS) == (16384, 256, 256)
    assert MAX_PATH_CHARS == 4096


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
        reason = out["permissionDecisionReason"]
        assert f"over the {MAX_COMMAND_CHARS} limit" in reason
        assert ".claude/hooks/hook_utils.py" in reason


# ---------------------------------------------------------------------------
# Over-length path and pattern fields
# ---------------------------------------------------------------------------

_PATH_GUARDS = ("env_guard.py", "vault_guard.py")


@pytest.mark.parametrize("guard", _PATH_GUARDS)
@pytest.mark.parametrize("tool, field", [
    (tool, field) for tool, fields in sorted(hook_utils.PATH_FIELDS.items()) for field in fields
])
@pytest.mark.parametrize("length, denied", [(MAX_PATH_CHARS, False), (MAX_PATH_CHARS + 1, True)])
def test_path_field_length_cap(guard, tool, field, length, denied, decide, hermetic_hook_project):
    value = "s/" + "a" * (length - 2)
    extra = {"pattern": "x"} if tool == "Grep" else {}
    out = decide(guard, hook_payload(tool, cwd=str(hermetic_hook_project), **{field: value, **extra}))
    assert (out is not None) == denied
    if denied:
        reason = out["permissionDecisionReason"]
        assert f"over the {MAX_PATH_CHARS} limit" in reason
        assert value not in reason  # lengths only, never the input


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


@pytest.mark.parametrize("pattern", [
    "{" + "a," * 100000 + "a}" + "x" * 100000,  # one wide set, long tail
    "{" + "a," * 300 + "a}" + "x" * 100000,      # just over the budget
], ids=["wide-set", "set-over-budget"])
def test_brace_set_is_counted_before_its_strings_are_built(pattern):
    """One set with N alternatives and a tail of M characters used to build
    N strings of M characters before the cap could fire (20 K x 20 K: 399
    MB). The set is now counted against the budget first."""
    env_guard = GUARDS["env_guard.py"]
    tracemalloc.start()
    start = time.perf_counter()
    try:
        with pytest.raises(env_guard.BraceLimit):
            env_guard._expand_braces(pattern)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert time.perf_counter() - start < 1.0
    assert peak < 50 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB"


def test_brace_set_at_the_budget_expands():
    """Control for the test above: a set of exactly MAX_BRACE_WORDS
    alternatives is expanded, not refused."""
    pattern = "{" + "a," * (MAX_BRACE_WORDS - 1) + "a}" + "x" * 1000
    assert len(GUARDS["env_guard.py"]._expand_braces(pattern)) == MAX_BRACE_WORDS


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
# Heredoc scanning stays linear
# ---------------------------------------------------------------------------

def _last_separator_reference(text: str) -> int:
    """The per-prefix scan drop_inert_heredocs used to run once per heredoc
    (hook_utils._last_separator before the separator index), kept as the
    reference the index must reproduce."""
    sep, quote, i, depth = -1, "", 0, 0
    resume: list[tuple[int, str]] = []
    while i < len(text):
        ch = text[i]
        if quote == '"' and ch == "$" and text[i + 1 : i + 2] == "(":
            resume.append((depth, quote))
            depth, quote, sep, i = 0, "", i + 1, i + 2
            continue
        size, after = hook_utils.shell_step(text, i, quote)
        if not quote and not after:
            if ch == ")":
                if depth:
                    depth -= 1
                elif resume:
                    depth, after = resume.pop()
            elif ch in (";", "&", "|", "`", "\n"):
                sep = i
            elif ch == "(":
                depth, sep = depth + 1, i
        quote, i = after, i + size
    return sep


def test_separator_index_matches_a_scan_of_every_prefix():
    rng = random.Random(318)
    alphabet = "ab <$()\\'\"`;&|\n"
    texts = ['-m "$(cat <<\'EOF\'\nx\nEOF\n)"', 'a "$(b)" c; d', "x\\'y 'z' \"$(", "\\"]
    texts += ["".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40))) for _ in range(2000)]
    for text in texts:
        seps, _ = hook_utils._separator_index(text)
        assert len(seps) == len(text) + 1
        for p in range(len(text) + 1):
            assert seps[p] == _last_separator_reference(text[:p]), (text, p)


_HEREDOC_FLOODS = {
    "dense": "<<A\nA\n" * 2730,
    "open-quote": "'" + "<<A\nA\n" * 2729,
    "balanced-quote": "<<A'\nA\n'" * 1820,
}


@pytest.mark.parametrize("guard", SHELL_GUARDS)
@pytest.mark.parametrize("name", sorted(_HEREDOC_FLOODS))
def test_hook_process_checks_a_heredoc_flood_in_time(guard, name, hermetic_hook_project):
    """Under MAX_COMMAND_CHARS, these took 4-8 s (past the 5 s hook timeout)
    when each heredoc rescanned the text before it."""
    command = _HEREDOC_FLOODS[name]
    assert len(command) <= MAX_COMMAND_CHARS
    payload = hook_payload("Bash", cwd=str(hermetic_hook_project), command=command)
    start = time.perf_counter()
    result = run_hook(guard, payload, cwd=hermetic_hook_project)
    elapsed = time.perf_counter() - start
    assert result.returncode == 0 and "could not complete" not in result.stdout
    assert elapsed < 3.0, f"took {elapsed:.2f} s"


_ENV_NAME = "." + "env"


@pytest.mark.parametrize("words, denied", [(1, False), (600, True)], ids=["short", "over-bound"])
def test_heredoc_after_a_long_command_keeps_its_body_scanned(words, denied, decide, hermetic_hook_project):
    """An inert heredoc body is dropped before the .env check. Past
    hook_utils._MAX_HEREDOC_COMMAND characters of command before `<<`, the
    heredoc counts as not inert, so its body is still checked."""
    command = "cat " + "a " * words + f"<<'EOF'\nsee {_ENV_NAME}\nEOF\n"
    out = decide("env_guard.py", hook_payload("Bash", cwd=str(hermetic_hook_project), command=command))
    assert (out is not None) == denied


# ---------------------------------------------------------------------------
# run_hook fails closed on anything raised
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("exc", [SystemExit(1), KeyboardInterrupt(), RuntimeError("boom")],
                         ids=["SystemExit", "KeyboardInterrupt", "RuntimeError"])
def test_run_hook_denies_whatever_the_check_raises(exc, monkeypatch, capsys):
    def evaluate(_payload):
        raise exc

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_payload("Read", file_path="src/a.py"))))
    hook_utils.run_hook("test_guard", evaluate, str)  # must not raise
    out = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    assert type(exc).__name__ in out["permissionDecisionReason"]
    assert "boom" not in out["permissionDecisionReason"]


def test_run_hook_allows_when_the_check_passes(monkeypatch, capsys):
    """Control for the test above: the same call with a passing check."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_payload("Read", file_path="src/a.py"))))
    hook_utils.run_hook("test_guard", lambda _payload: None, str)
    assert capsys.readouterr().out == ""


def test_run_hook_exits_2_when_the_deny_cannot_be_written(monkeypatch):
    """Exit code 2 is the harness's own block signal."""
    def broken_deny(_reason):
        raise OSError("stdout closed")

    monkeypatch.setattr(hook_utils, "deny", broken_deny)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_payload("Read", file_path="src/a.py"))))
    with pytest.raises(SystemExit) as raised:
        hook_utils.run_hook("test_guard", lambda _payload: "target", str)
    assert raised.value.code == 2


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
