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

from tests.conftest import hook_output, hook_payload, load_hook, payload_id, run_hook

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = REPO_ROOT / ".claude" / "settings.json"


env_guard = load_hook("env_guard.py")


def _run(payload) -> subprocess.CompletedProcess:
    return run_hook("env_guard.py", payload)


# ---------------------------------------------------------------------------
# Deny / allow tables (in-process evaluate(); fast)
# ---------------------------------------------------------------------------

DENY = [
    # Read -- Windows, POSIX, Git Bash, relative, case, Win32 aliases
    hook_payload("Read", file_path=r"C:\dev\proj\.env"),
    hook_payload("Read", file_path="C:/dev/proj/.env"),
    hook_payload("Read", file_path="/c/dev/proj/.env"),
    hook_payload("Read", file_path="/home/user/proj/.env"),
    hook_payload("Read", file_path=".env"),
    hook_payload("Read", file_path=r"C:\dev\proj\.env.local"),
    hook_payload("Read", file_path="/srv/app/.env.production"),
    hook_payload("Read", file_path=r"C:\dev\proj\.ENV"),
    hook_payload("Read", file_path=r"C:\dev\proj\.env."),
    hook_payload("Read", file_path=r"C:\dev\proj\.env::$DATA"),
    hook_payload("Read", file_path="C:.env"),
    # Edit / Write
    hook_payload("Write", file_path=r"C:\dev\proj\.env"),
    hook_payload("Write", file_path=r"C:\dev\proj\.env.local"),
    hook_payload("Write", file_path="/c/dev/proj/.env.production"),
    hook_payload("Edit", file_path=r"C:\dev\proj\.ENV.local", old_string="a", new_string="b"),
    hook_payload("Edit", file_path="/home/user/proj/.env", old_string="a", new_string="b"),
    # Grep -- path field, glob field (dotfile rule and ripgrep rule)
    hook_payload("Grep", pattern="KEY", path=r"C:\dev\proj\.env"),
    hook_payload("Grep", pattern="KEY", path="/c/dev/proj/.env.local"),
    hook_payload("Grep", pattern="KEY", path=".", glob=".env*"),
    hook_payload("Grep", pattern="KEY", path=".", glob="**/.env"),
    hook_payload("Grep", pattern="KEY", path=".", glob=".env{,.local}"),
    hook_payload("Grep", pattern="KEY", path=".", glob="*"),
    hook_payload("Grep", pattern="KEY", path=".", glob="*.local"),
    hook_payload("Grep", pattern="KEY", path=".", glob="*env*"),
    # Glob -- pattern and path fields
    hook_payload("Glob", pattern=".env*"),
    hook_payload("Glob", pattern="**/.env"),
    hook_payload("Glob", pattern=".e*"),
    hook_payload("Glob", pattern=".en?"),
    hook_payload("Glob", pattern=".[e]nv"),
    hook_payload("Glob", pattern=r"C:\dev\proj\.env.*"),
    hook_payload("Glob", pattern="*", path=r"C:\dev\proj\.env"),
    # Bash -- any reader, absolute-path utilities, Windows paths
    hook_payload("Bash", command="cat .env"),
    hook_payload("Bash", command="tail -c 200 .env | xxd"),
    hook_payload("Bash", command=r"C:\Windows\System32\more.com C:\x\.env"),
    hook_payload("Bash", command="/usr/bin/cat /home/user/proj/.env.local"),
    hook_payload("Bash", command="sed -n 1,5p .env"),
    hook_payload("Bash", command='grep -no "^[A-Z_]*KEY" .env'),
    hook_payload("Bash", command="findstr KEY .env.local"),
    hook_payload("Bash", command="awk '{print length($0)}' ./.env"),
    hook_payload("Bash", command="cd /c/dev/proj && cat .env.production"),
    hook_payload("Bash", command="echo $(cat .env)"),
    hook_payload("Bash", command="python -c \"print(open('.env').read())\""),
    hook_payload("Bash", command="node script.js --env-file=.env"),
    hook_payload("Bash", command="cat .env.local; echo done"),
    hook_payload("Bash", command="printf 'X=1' >> .env"),
    # Bash -- prose skip does not hide a path outside the quoted message
    hook_payload("Bash", command='git commit -m "msg" .env'),
    hook_payload("Bash", command='echo -m ".env"'),
    # PowerShell
    hook_payload("PowerShell", command="Get-Content .env"),
    hook_payload("PowerShell", command=r"gc .\.env.local"),
    hook_payload("PowerShell", command=r"Get-Content -Path C:\dev\proj\.env -Raw"),
    hook_payload("PowerShell", command=r"type C:\dev\proj\.env"),
    hook_payload("PowerShell", command="Select-String -Path .env -Pattern KEY"),
    hook_payload("PowerShell", command=r"[IO.File]::ReadAllText('C:\dev\proj\.env')"),
    # Globs that can reach a .env name. Pinned with the jq/regex allow cases
    # below: the fix for those must not reopen any of these.
    hook_payload("Bash", command="cat .*"),                       # unquoted: the shell expands it
    hook_payload("Bash", command="cat .[e]nv"),
    hook_payload("Bash", command="cat .?nv.local"),
    hook_payload("Bash", command="cat .[!x]nv"),
    hook_payload("Bash", command="head ./.e*"),
    hook_payload("Bash", command='echo "$(cat .*)"'),             # substitution runs in the shell
    hook_payload("Bash", command='echo "`cat .*`"'),
    hook_payload("Bash", command="bash -c 'cat .*'"),             # a nested shell expands it
    hook_payload("Bash", command="sh -c 'cat .[e]nv'"),
    hook_payload("Bash", command="find . -name '.*' -exec cat {} +"),
    hook_payload("Bash", command='find . -iname ".*" | xargs cat'),
    hook_payload("Bash", command="rg -g '.*' KEY"),
    hook_payload("Bash", command="rg --glob='.*' KEY"),
    hook_payload("Bash", command="rg --glob '.e*' KEY"),
    hook_payload("Bash", command="cat '.env'"),                   # quoting does not hide a literal name
    hook_payload("Bash", command="cat '.env.local'"),
    hook_payload("Bash", command="cat '.env*'"),
    hook_payload("Bash", command='cat ".e*"'),
    hook_payload("Bash", command="sed 's/x/y/' '.env'"),
    hook_payload("PowerShell", command="gc '.*'"),                # cmdlets expand quoted wildcards
    hook_payload("PowerShell", command='Get-Content ".[e]nv"'),
]

ALLOW = [
    # .env.example everywhere
    hook_payload("Read", file_path=r"C:\dev\proj\.env.example"),
    hook_payload("Read", file_path="/c/dev/proj/.env.example"),
    hook_payload("Grep", pattern="KEY", path=r"C:\dev\proj\.env.example"),
    hook_payload("Grep", pattern="KEY", path=".", glob=".env.example"),
    hook_payload("Grep", pattern="KEY", path=".", glob=".env.example*"),
    hook_payload("Glob", pattern="**/.env.example"),
    hook_payload("Glob", pattern=".env.example*"),
    hook_payload("Write", file_path=r"C:\dev\proj\.env.example"),
    hook_payload("Edit", file_path="/c/dev/proj/.env.example", old_string="a", new_string="b"),
    hook_payload("Bash", command="cat .env.example"),
    hook_payload("Bash", command=r"C:\Windows\System32\more.com C:\x\.env.example"),
    hook_payload("PowerShell", command=r"Get-Content C:\dev\proj\.env.example"),
    # Unrelated names that merely contain "env"
    hook_payload("Read", file_path=r"C:\dev\proj\src\core\environment.py"),
    hook_payload("Read", file_path="/c/dev/proj/.envrc"),
    hook_payload("Read", file_path="/c/dev/proj/tests/test_env_file_loading.py"),
    hook_payload("Read", file_path="/c/dev/proj/.venv/pyvenv.cfg"),
    hook_payload("Bash", command="ls src/core"),
    # Grep: content pattern is not a path; ordinary globs
    hook_payload("Grep", pattern=r"\.env", path="src"),
    hook_payload("Grep", pattern="KEY", path=".", glob="*.py"),
    hook_payload("Grep", pattern="KEY", path=".", glob="**/*.md"),
    hook_payload("Grep", pattern="KEY", path=".", glob="*.{ts,tsx}"),
    hook_payload("Grep", pattern="KEY", path=".", glob="!*.py"),
    # Glob: plain `*` does not reach dotfiles; other dot-globs
    hook_payload("Glob", pattern="**/*.py"),
    hook_payload("Glob", pattern="*"),
    hook_payload("Glob", pattern=".git*"),
    # Prose in git/gh message flags
    hook_payload("Bash", command='git commit -m "chore(security): block agent reads of .env files"'),
    hook_payload("Bash", command="git commit -am 'mentions .env.local in prose'"),
    hook_payload("Bash", command='gh pr create --title "Block .env reads" --body "Denies .env and .env.* reads"'),
    hook_payload("Bash", command='gh release create v1 --notes "no .env changes"'),
    hook_payload("Bash", command='git commit --message=".env guard"'),
    hook_payload(
        "Bash",
        command=(
            'git commit -m "$(cat <<\'EOF\'\n'
            "fix: mentions .env in the commit body as prose text\n"
            "EOF\n"
            ')"'
        ),
    ),
    # jq paths and regex text: dot-led, but not a .env name
    hook_payload("Bash", command="jq '.[0]' data.json"),
    hook_payload("Bash", command="jq .[0] data.json"),             # a glob that cannot match .env
    hook_payload("Bash", command="jq -r '.[0].name' data.json"),
    hook_payload("Bash", command="jq '.[] | .id' data.json"),
    hook_payload("Bash", command="gh pr view 1 --json files --jq '.files[].path'"),
    hook_payload("Bash", command="grep -E '.*KEY' notes.txt"),
    hook_payload("Bash", command="grep '.*' notes.txt"),
    hook_payload("Bash", command="sed -n 's/.*VALUE//p' notes.txt"),
    hook_payload("Bash", command='rg ".*foo" src'),
    hook_payload("Bash", command="python -c \"import re; print(re.sub(r'.*', '', 'x'))\""),
    hook_payload("Bash", command="cat .e[x]"),                     # cannot match .env
    hook_payload("PowerShell", command="Get-Content data.json | jq '.[0]'"),
    # Tools the guard does not own
    hook_payload("WebFetch", url="https://example.com/.env"),
]


@pytest.mark.parametrize("payload", DENY, ids=payload_id)
def test_denies(payload):
    assert env_guard.evaluate(payload) is not None


@pytest.mark.parametrize("payload", ALLOW, ids=payload_id)
def test_allows(payload):
    assert env_guard.evaluate(payload) is None


# ---------------------------------------------------------------------------
# The glob rule: can a dot-led glob match a .env name?
# ---------------------------------------------------------------------------

GLOB_REACHES = [".*", ".e*", ".en?", ".env*", ".[e]nv", ".[!x]nv", ".[a-f]nv*", ".?nv.local",
                ".env.s*", ".*.local", ".env?", ".e?v", ".*l", ".**"]
GLOB_CANNOT = [".[0]", ".[]", ".[0].name", ".e[x]", ".git*", ".[!e]nv", ".env[!.]x", ".?", ".e[!n]*"]
_PROBES = (".env", ".env.local", ".env.production", ".env.staging", ".env.l", ".env.test")


@pytest.mark.parametrize("glob", GLOB_REACHES)
def test_glob_that_can_match_a_env_name_is_detected(glob):
    assert env_guard._glob_can_name_env(glob)


@pytest.mark.parametrize("glob", GLOB_CANNOT)
def test_glob_that_cannot_match_a_env_name_is_not(glob):
    assert not env_guard._glob_can_name_env(glob)
    # Cross-check against fnmatch: no probe name matches either.
    import fnmatch

    assert not any(fnmatch.fnmatchcase(p, glob) for p in _PROBES)


def _old_literal_prefix_rule(glob: str) -> bool:
    """The rule this replaced: the literal text before the first wildcard is
    compatible with `.env`."""
    literal = glob[: env_guard._WILDCARD.search(glob).start()]
    return literal.startswith(".env") or ".env".startswith(literal)


@pytest.mark.parametrize("glob", GLOB_REACHES + GLOB_CANNOT)
def test_glob_rule_only_narrows_the_old_rule(glob):
    """Nothing the old literal-prefix rule allowed is denied now, and every
    glob that fnmatch shows reaching a probe .env name is still denied."""
    import fnmatch

    if env_guard._glob_can_name_env(glob):
        assert _old_literal_prefix_rule(glob)
    if any(fnmatch.fnmatchcase(p, glob) for p in _PROBES):
        assert env_guard._glob_can_name_env(glob)


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
        hook_payload("Read", file_path=r"C:\dev\proj\.env.local"),
        hook_payload("Read", file_path=r"C:\dev\proj\.env.example"),
    ),
    "Grep": (
        hook_payload("Grep", pattern="KEY", path="/c/dev/proj/.env"),
        hook_payload("Grep", pattern="KEY", path="/c/dev/proj/.env.example"),
    ),
    "Glob": (
        hook_payload("Glob", pattern="**/.env*"),
        hook_payload("Glob", pattern="**/.env.example"),
    ),
    "Write": (
        hook_payload("Write", file_path=r"C:\dev\proj\.env.local"),
        hook_payload("Write", file_path=r"C:\dev\proj\.env.example"),
    ),
    "Bash": (
        hook_payload("Bash", command=r"C:\Windows\System32\more.com C:\x\.env"),
        hook_payload("Bash", command="cat .env.example"),
    ),
    "PowerShell": (
        hook_payload("PowerShell", command=r"gc C:\dev\proj\.env.production"),
        hook_payload("PowerShell", command=r"gc C:\dev\proj\.env.example"),
    ),
}


@pytest.mark.parametrize("tool", sorted(E2E))
def test_positive_control_hook_process_denies(tool):
    deny_payload, _ = E2E[tool]
    result = _run(deny_payload)
    assert result.returncode == 0
    assert hook_output(result)["permissionDecision"] == "deny"
    reason = hook_output(result)["permissionDecisionReason"]
    assert ".env.example" in reason


@pytest.mark.parametrize("tool", sorted(E2E))
def test_hook_process_allows_example(tool):
    _, allow_payload = E2E[tool]
    result = _run(allow_payload)
    assert result.returncode == 0
    assert hook_output(result) is None


@pytest.mark.parametrize("raw", ["", "not json", "[]", json.dumps({"tool_name": "Read"})])
def test_malformed_payload_allows_without_crashing(raw):
    result = _run(raw)
    assert result.returncode == 0
    assert hook_output(result) is None


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
        ("env_guard.py", hook_payload("Read", file_path=".env.local")),
        ("vault_guard.py", hook_payload("Write", file_path="private_vault/x.json")),
        ("secret_dump_guard.py", hook_payload("Bash", command="cat private_vault/x.json")),
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
    assert hook_output(result)["permissionDecision"] == "deny"
