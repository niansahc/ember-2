"""
tests/test_secret_dump_guard.py

Regression tests for .claude/hooks/secret_dump_guard.py (issue #188).

Invokes the hook the same way the Claude Code hook harness does: a JSON
tool_input on stdin, a deny decision (or silence) on stdout. This is
PreToolUse prevention of known dump-command forms against private_vault/
paths -- not redaction, and not a guarantee. See the hook's own module
docstring for why.

.env is owned by env_guard.py (tests/test_env_guard.py). The hand-off is
tested here: this hook passes .env commands through, and env_guard denies
the same commands.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[1] / ".claude" / "hooks"
HOOK_PATH = HOOKS_DIR / "secret_dump_guard.py"
ENV_GUARD_PATH = HOOKS_DIR / "env_guard.py"


def _run_hook(command: str, hook: Path = HOOK_PATH, tool: str = "Bash") -> dict | None:
    """Run a hook with a synthetic shell tool_input, return the parsed
    decision dict, or None if the hook produced no output (allow)."""
    payload = json.dumps({
        "tool_name": tool,
        "tool_input": {"command": command},
    })
    result = subprocess.run(
        [sys.executable, str(hook)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    stdout = result.stdout.strip()
    if not stdout:
        return None
    return json.loads(stdout)


def _is_denied(command: str, hook: Path = HOOK_PATH, tool: str = "Bash") -> bool:
    decision = _run_hook(command, hook, tool)
    if decision is None:
        return False
    return (
        decision.get("hookSpecificOutput", {}).get("permissionDecision")
        == "deny"
    )


class TestDeniesKnownDumpForms:
    def test_xxd_vault_file(self):
        assert _is_denied("xxd private_vault/memory/x.json")

    def test_cat_vault_file(self):
        assert _is_denied("cat private_vault/memory/x.json")

    def test_tail_piped_to_xxd(self):
        """The #188 incident form, against a vault path."""
        assert _is_denied("tail -c 200 private_vault/memory/x.json | xxd")

    def test_hexdump_private_vault_file(self):
        assert _is_denied("hexdump private_vault/some_file.json")

    def test_get_content_vault_file(self):
        assert _is_denied("Get-Content private_vault/memory/x.json")

    def test_type_vault_file(self):
        assert _is_denied("type private_vault/memory/x.json")

    def test_less_private_vault_path(self):
        assert _is_denied("less private_vault/notes.json")

    def test_windows_backslash_vault_path(self):
        """shlex POSIX mode used to eat the backslashes, turning the path
        into C:xprivate_vaulta.json and missing it."""
        assert _is_denied(r"cat C:\dev\proj\private_vault\memory\a.json")

    def test_absolute_path_windows_utility(self):
        assert _is_denied(r"C:\Windows\System32\more.com C:\x\private_vault\a.json")

    def test_absolute_path_posix_utility(self):
        assert _is_denied("/usr/bin/cat /c/dev/proj/private_vault/a.json")

    def test_exe_suffix_and_case(self):
        assert _is_denied("CAT.EXE private_vault/a.json")
        assert _is_denied("GET-CONTENT private_vault/a.json")

    def test_powershell_tool_gc_alias(self):
        assert _is_denied(r"gc C:\dev\proj\private_vault\a.json", tool="PowerShell")

    def test_deny_reason_names_the_utility_and_is_not_silent(self):
        decision = _run_hook("cat private_vault/a.json")
        assert decision is not None
        reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
        assert "cat" in reason
        assert "#188" in reason


class TestAllowsSafePatterns:
    """Over-blocking guard. Each has a deny counterpart above (same hook,
    same vault path, dump utility instead), so these are not vacuous."""

    def test_word_count(self):
        assert not _is_denied("wc -l private_vault/memory/x.json")

    def test_list_dir(self):
        assert not _is_denied("ls -la private_vault/memory/")

    def test_unrelated_command_no_protected_path(self):
        assert not _is_denied("cat README.md")

    def test_utility_name_as_substring_does_not_false_positive(self):
        """'category'/'heading' must not match the 'cat'/'head' blocklist."""
        assert not _is_denied("echo 'category heading' private_vault/x")

    def test_heredoc_mentioning_vault_in_prose_is_not_flagged(self):
        command = (
            'git commit -m "$(cat <<\'EOF\'\n'
            "fix: mentions private_vault/ in the commit body as prose text\n"
            "EOF\n"
            ')"'
        )
        assert not _is_denied(command)

    def test_dump_utility_and_protected_path_in_different_segments(self):
        assert not _is_denied("cat README.md && echo done private_vault/x")


class TestEnvHandOffToEnvGuard:
    """.env moved to env_guard.py. This hook no longer substring-matches
    it (which wrongly denied .env.example); env_guard denies .env itself."""

    def test_dot_env_example_not_denied_here(self):
        assert not _is_denied("cat .env.example")

    def test_dot_env_passes_this_hook(self):
        assert not _is_denied("cat .env")

    def test_positive_control_env_guard_denies_same_command(self):
        assert _is_denied("cat .env", hook=ENV_GUARD_PATH)
        assert not _is_denied("cat .env.example", hook=ENV_GUARD_PATH)


class TestMalformedInput:
    def test_empty_stdin_does_not_crash(self):
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input="",
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_missing_command_field_does_not_crash(self):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {}})
        result = subprocess.run(
            [sys.executable, str(HOOK_PATH)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""
