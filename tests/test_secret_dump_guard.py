"""
tests/test_secret_dump_guard.py

Regression tests for .claude/hooks/secret_dump_guard.py (issue #188).

PreToolUse prevention of known dump-command forms against private_vault/
paths -- not redaction, and not a guarantee. See the hook's own module
docstring for why. .env is owned by env_guard.py (tests/test_env_guard.py).

Matching is tested in-process through _matches_dump_command(); a few
end-to-end cases run the hook the way the harness does (JSON on stdin,
a deny decision or silence on stdout).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "secret_dump_guard.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("secret_dump_guard_under_test", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_hook()


def _run_hook(raw: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=raw,
        capture_output=True,
        text=True,
        timeout=10,
    )


DENY = [
    "xxd private_vault/memory/x.json",
    "cat private_vault/memory/x.json",
    # The #188 incident form, against a vault path.
    "tail -c 200 private_vault/memory/x.json | xxd",
    "hexdump private_vault/some_file.json",
    "Get-Content private_vault/memory/x.json",
    "type private_vault/memory/x.json",
    "less private_vault/notes.json",
    # shlex POSIX mode used to eat backslashes (C:xprivate_vaulta.json).
    r"cat C:\dev\proj\private_vault\memory\a.json",
    r"C:\Windows\System32\more.com C:\x\private_vault\a.json",
    "/usr/bin/cat /c/dev/proj/private_vault/a.json",
    "CAT.EXE private_vault/a.json",
    "GET-CONTENT private_vault/a.json",
    r"gc C:\dev\proj\private_vault\a.json",
]

# Over-blocking guard. Each is an absence check; DENY above is the
# positive control (same function, same vault path, dump utility instead).
ALLOW = [
    "wc -l private_vault/memory/x.json",
    "ls -la private_vault/memory/",
    "cat README.md",
    # 'category'/'heading' must not match the 'cat'/'head' blocklist.
    "echo 'category heading' private_vault/x",
    # Prose inside a quoted commit message is not a command.
    (
        'git commit -m "$(cat <<\'EOF\'\n'
        "fix: mentions private_vault/ in the commit body as prose text\n"
        "EOF\n"
        ')"'
    ),
    # Utility and path in different segments must not cross-contaminate.
    "cat README.md && echo done private_vault/x",
    # .env moved to env_guard.py; the old substring match denied the example.
    "cat .env.example",
]


@pytest.mark.parametrize("command", DENY)
def test_denies_known_dump_forms(command):
    assert guard._matches_dump_command(command) is not None


@pytest.mark.parametrize("command", ALLOW)
def test_allows_safe_patterns(command):
    assert guard._matches_dump_command(command) is None


def test_hook_process_denies_with_reason():
    payload = json.dumps({"tool_name": "PowerShell", "tool_input": {"command": "gc private_vault/a.json"}})
    result = _run_hook(payload)
    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "gc" in output["permissionDecisionReason"]
    assert "#188" in output["permissionDecisionReason"]


@pytest.mark.parametrize(
    "raw",
    ["", json.dumps({"tool_name": "Bash", "tool_input": {}})],
    ids=["empty-stdin", "missing-command"],
)
def test_malformed_input_does_not_crash(raw):
    result = _run_hook(raw)
    assert result.returncode == 0
    assert result.stdout.strip() == ""
