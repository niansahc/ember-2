"""
tests/test_env_guard.py

Tests for the .env protection hooks:
  - .claude/hooks/env_guard.py   (Read|Grep|Glob|Edit|Write|Bash|PowerShell)
  - .claude/settings.json        (matcher coverage for every guard,
                                  cwd-independent commands)

vault_guard.py has its own file, tests/test_vault_guard.py.

Every payload is synthetic hook JSON. No test opens, reads, or names the
repo's real .env through a file API; .env names below are only strings
fed to a pattern check. Paths are given in Windows, POSIX and Git Bash
(/c/...) forms because the same agent produces all three.

Absence tests (allow cases) are paired with positive controls: each tool
gets an end-to-end deny through the real hook process, so an allow result
means "the hook ran and passed this," not "the hook never ran."
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import load_hook, run_hook

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"


env_guard = load_hook("env_guard.py")


def _payload(tool: str, **tool_input) -> dict:
    return {"tool_name": tool, "tool_input": tool_input}


def _run(payload) -> subprocess.CompletedProcess:
    return run_hook("env_guard.py", payload)


def _decision(result: subprocess.CompletedProcess) -> str | None:
    out = result.stdout.strip()
    if not out:
        return None
    return json.loads(out)["hookSpecificOutput"]["permissionDecision"]


# ---------------------------------------------------------------------------
# Deny / allow tables (in-process evaluate(); fast)
# ---------------------------------------------------------------------------

DENY = [
    # Read -- Windows, POSIX, Git Bash, relative, case, Win32 aliases
    _payload("Read", file_path=r"C:\dev\proj\.env"),
    _payload("Read", file_path="C:/dev/proj/.env"),
    _payload("Read", file_path="/c/dev/proj/.env"),
    _payload("Read", file_path="/home/user/proj/.env"),
    _payload("Read", file_path=".env"),
    _payload("Read", file_path=r"C:\dev\proj\.env.local"),
    _payload("Read", file_path="/srv/app/.env.production"),
    _payload("Read", file_path=r"C:\dev\proj\.ENV"),
    _payload("Read", file_path=r"C:\dev\proj\.env."),
    _payload("Read", file_path=r"C:\dev\proj\.env::$DATA"),
    _payload("Read", file_path="C:.env"),
    # Edit / Write
    _payload("Write", file_path=r"C:\dev\proj\.env"),
    _payload("Write", file_path=r"C:\dev\proj\.env.local"),
    _payload("Write", file_path="/c/dev/proj/.env.production"),
    _payload("Edit", file_path=r"C:\dev\proj\.ENV.local", old_string="a", new_string="b"),
    _payload("Edit", file_path="/home/user/proj/.env", old_string="a", new_string="b"),
    # Grep -- path field, glob field (dotfile rule and ripgrep rule)
    _payload("Grep", pattern="KEY", path=r"C:\dev\proj\.env"),
    _payload("Grep", pattern="KEY", path="/c/dev/proj/.env.local"),
    _payload("Grep", pattern="KEY", path=".", glob=".env*"),
    _payload("Grep", pattern="KEY", path=".", glob="**/.env"),
    _payload("Grep", pattern="KEY", path=".", glob=".env{,.local}"),
    _payload("Grep", pattern="KEY", path=".", glob="*"),
    _payload("Grep", pattern="KEY", path=".", glob="*.local"),
    _payload("Grep", pattern="KEY", path=".", glob="*env*"),
    # Glob -- pattern and path fields
    _payload("Glob", pattern=".env*"),
    _payload("Glob", pattern="**/.env"),
    _payload("Glob", pattern=".e*"),
    _payload("Glob", pattern=".en?"),
    _payload("Glob", pattern=".[e]nv"),
    _payload("Glob", pattern=r"C:\dev\proj\.env.*"),
    _payload("Glob", pattern="*", path=r"C:\dev\proj\.env"),
    # Bash -- any reader, absolute-path utilities, Windows paths
    _payload("Bash", command="cat .env"),
    _payload("Bash", command="tail -c 200 .env | xxd"),
    _payload("Bash", command=r"C:\Windows\System32\more.com C:\x\.env"),
    _payload("Bash", command="/usr/bin/cat /home/user/proj/.env.local"),
    _payload("Bash", command="sed -n 1,5p .env"),
    _payload("Bash", command='grep -no "^[A-Z_]*KEY" .env'),
    _payload("Bash", command="findstr KEY .env.local"),
    _payload("Bash", command="awk '{print length($0)}' ./.env"),
    _payload("Bash", command="cd /c/dev/proj && cat .env.production"),
    _payload("Bash", command="echo $(cat .env)"),
    _payload("Bash", command="python -c \"print(open('.env').read())\""),
    _payload("Bash", command="node script.js --env-file=.env"),
    _payload("Bash", command="cat .env.local; echo done"),
    _payload("Bash", command="printf 'X=1' >> .env"),
    # Bash -- prose skip does not hide a path outside the quoted message
    _payload("Bash", command='git commit -m "msg" .env'),
    _payload("Bash", command='echo -m ".env"'),
    # PowerShell
    _payload("PowerShell", command="Get-Content .env"),
    _payload("PowerShell", command=r"gc .\.env.local"),
    _payload("PowerShell", command=r"Get-Content -Path C:\dev\proj\.env -Raw"),
    _payload("PowerShell", command=r"type C:\dev\proj\.env"),
    _payload("PowerShell", command="Select-String -Path .env -Pattern KEY"),
    _payload("PowerShell", command=r"[IO.File]::ReadAllText('C:\dev\proj\.env')"),
]

ALLOW = [
    # .env.example everywhere
    _payload("Read", file_path=r"C:\dev\proj\.env.example"),
    _payload("Read", file_path="/c/dev/proj/.env.example"),
    _payload("Grep", pattern="KEY", path=r"C:\dev\proj\.env.example"),
    _payload("Grep", pattern="KEY", path=".", glob=".env.example"),
    _payload("Grep", pattern="KEY", path=".", glob=".env.example*"),
    _payload("Glob", pattern="**/.env.example"),
    _payload("Glob", pattern=".env.example*"),
    _payload("Write", file_path=r"C:\dev\proj\.env.example"),
    _payload("Edit", file_path="/c/dev/proj/.env.example", old_string="a", new_string="b"),
    _payload("Bash", command="cat .env.example"),
    _payload("Bash", command=r"C:\Windows\System32\more.com C:\x\.env.example"),
    _payload("PowerShell", command=r"Get-Content C:\dev\proj\.env.example"),
    # Unrelated names that merely contain "env"
    _payload("Read", file_path=r"C:\dev\proj\src\core\environment.py"),
    _payload("Read", file_path="/c/dev/proj/.envrc"),
    _payload("Read", file_path="/c/dev/proj/tests/test_env_file_loading.py"),
    _payload("Read", file_path="/c/dev/proj/.venv/pyvenv.cfg"),
    _payload("Bash", command="ls src/core"),
    # Grep: content pattern is not a path; ordinary globs
    _payload("Grep", pattern=r"\.env", path="src"),
    _payload("Grep", pattern="KEY", path=".", glob="*.py"),
    _payload("Grep", pattern="KEY", path=".", glob="**/*.md"),
    _payload("Grep", pattern="KEY", path=".", glob="*.{ts,tsx}"),
    _payload("Grep", pattern="KEY", path=".", glob="!*.py"),
    # Glob: plain `*` does not reach dotfiles; other dot-globs
    _payload("Glob", pattern="**/*.py"),
    _payload("Glob", pattern="*"),
    _payload("Glob", pattern=".git*"),
    # Prose in git/gh message flags
    _payload("Bash", command='git commit -m "chore(security): block agent reads of .env files"'),
    _payload("Bash", command="git commit -am 'mentions .env.local in prose'"),
    _payload("Bash", command='gh pr create --title "Block .env reads" --body "Denies .env and .env.* reads"'),
    _payload("Bash", command='gh release create v1 --notes "no .env changes"'),
    _payload("Bash", command='git commit --message=".env guard"'),
    _payload(
        "Bash",
        command=(
            'git commit -m "$(cat <<\'EOF\'\n'
            "fix: mentions .env in the commit body as prose text\n"
            "EOF\n"
            ')"'
        ),
    ),
    # Tools the guard does not own
    _payload("WebFetch", url="https://example.com/.env"),
]


def _id(p: dict) -> str:
    return f"{p['tool_name']}:{json.dumps(p['tool_input'])[:70]}"


@pytest.mark.parametrize("payload", DENY, ids=_id)
def test_denies(payload):
    assert env_guard.evaluate(payload) is not None


@pytest.mark.parametrize("payload", ALLOW, ids=_id)
def test_allows(payload):
    assert env_guard.evaluate(payload) is None


def test_every_guarded_tool_has_deny_and_allow_cases():
    """The tables themselves stay balanced: a tool with allow cases but no
    deny case would make its allow cases vacuous."""
    guarded = {"Read", "Grep", "Glob", "Edit", "Write", "Bash", "PowerShell"}
    assert guarded <= {p["tool_name"] for p in DENY}
    assert guarded <= {p["tool_name"] for p in ALLOW}


# ---------------------------------------------------------------------------
# End-to-end through the real hook process, one positive control per tool
# ---------------------------------------------------------------------------

E2E = {
    "Read": (
        _payload("Read", file_path=r"C:\dev\proj\.env.local"),
        _payload("Read", file_path=r"C:\dev\proj\.env.example"),
    ),
    "Grep": (
        _payload("Grep", pattern="KEY", path="/c/dev/proj/.env"),
        _payload("Grep", pattern="KEY", path="/c/dev/proj/.env.example"),
    ),
    "Glob": (
        _payload("Glob", pattern="**/.env*"),
        _payload("Glob", pattern="**/.env.example"),
    ),
    "Write": (
        _payload("Write", file_path=r"C:\dev\proj\.env.local"),
        _payload("Write", file_path=r"C:\dev\proj\.env.example"),
    ),
    "Bash": (
        _payload("Bash", command=r"C:\Windows\System32\more.com C:\x\.env"),
        _payload("Bash", command="cat .env.example"),
    ),
    "PowerShell": (
        _payload("PowerShell", command=r"gc C:\dev\proj\.env.production"),
        _payload("PowerShell", command=r"gc C:\dev\proj\.env.example"),
    ),
}


@pytest.mark.parametrize("tool", sorted(E2E))
def test_positive_control_hook_process_denies(tool):
    deny_payload, _ = E2E[tool]
    result = _run(deny_payload)
    assert result.returncode == 0
    assert _decision(result) == "deny"
    reason = json.loads(result.stdout)["hookSpecificOutput"]["permissionDecisionReason"]
    assert ".env.example" in reason


@pytest.mark.parametrize("tool", sorted(E2E))
def test_hook_process_allows_example(tool):
    _, allow_payload = E2E[tool]
    result = _run(allow_payload)
    assert result.returncode == 0
    assert _decision(result) is None


@pytest.mark.parametrize("raw", ["", "not json", "[]", json.dumps({"tool_name": "Read"})])
def test_malformed_payload_allows_without_crashing(raw):
    result = _run(raw)
    assert result.returncode == 0
    assert _decision(result) is None


# ---------------------------------------------------------------------------
# settings.json: matcher coverage and cwd-independent hook commands
# ---------------------------------------------------------------------------

def _hook_entries(event: str) -> list[tuple[str, str]]:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    return [
        (group["matcher"], hook["command"])
        for group in settings["hooks"][event]
        for hook in group["hooks"]
    ]


def _matcher_for(script_name: str) -> set[str]:
    tools: set[str] = set()
    for matcher, command in _hook_entries("PreToolUse"):
        if script_name in command:
            tools.update(matcher.split("|"))
    return tools


def _is_cwd_independent(command: str) -> bool:
    """Every path in the command is anchored on $CLAUDE_PROJECT_DIR."""
    return command.startswith('"$CLAUDE_PROJECT_DIR/') and ".py" in command and all(
        part.startswith("$CLAUDE_PROJECT_DIR/")
        for part in command.split('"')[1::2]
    )


@pytest.mark.parametrize(
    "script,tools",
    [
        ("env_guard.py", {"Read", "Grep", "Glob", "Edit", "Write", "Bash", "PowerShell"}),
        ("vault_guard.py", {"Read", "Grep", "Glob", "Edit", "Write"}),
        ("secret_dump_guard.py", {"Bash", "PowerShell"}),
    ],
)
def test_settings_matcher_covers_tools(script, tools):
    assert tools <= _matcher_for(script)


def test_settings_every_hook_command_is_cwd_independent():
    for event in ("PreToolUse", "PostToolUse"):
        for _, command in _hook_entries(event):
            assert _is_cwd_independent(command), command


def test_cwd_check_positive_control():
    """The pre-fix relative command fails the check, so the test above is
    not satisfied vacuously."""
    assert not _is_cwd_independent(".venv/Scripts/python.exe .claude/hooks/vault_guard.py")
    assert _is_cwd_independent(
        '"$CLAUDE_PROJECT_DIR/.venv/Scripts/python.exe" "$CLAUDE_PROJECT_DIR/.claude/hooks/vault_guard.py"'
    )


def _find_git_bash() -> str | None:
    """Claude Code runs hook commands through Git Bash on Windows. Prefer it
    explicitly: a bare `which bash` can resolve to the WSL launcher in
    System32, which would not see the Windows venv."""
    for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
        if Path(candidate).exists():
            return candidate
    found = shutil.which("bash")
    return found if found and "system32" not in found.lower() else None


_BASH = _find_git_bash()
_VENV_PY = REPO_ROOT / ".venv" / "Scripts" / "python.exe"


@pytest.mark.skipif(
    _BASH is None or not _VENV_PY.exists(),
    reason="needs bash and the Windows venv interpreter the settings commands name",
)
@pytest.mark.parametrize(
    "script,payload",
    [
        ("env_guard.py", _payload("Read", file_path=".env.local")),
        ("vault_guard.py", _payload("Write", file_path="private_vault/x.json")),
        ("secret_dump_guard.py", _payload("Bash", command="cat private_vault/x.json")),
    ],
)
def test_settings_command_fires_from_another_cwd(tmp_path, script, payload):
    """Run the exact settings.json command string the way the harness does
    (bash), from a directory that is not the project root."""
    command = next(c for _, c in _hook_entries("PreToolUse") if script in c)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(REPO_ROOT)}
    result = subprocess.run(
        [_BASH, "-c", command],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=20,
        cwd=tmp_path,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert _decision(result) == "deny"
