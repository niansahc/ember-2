"""
tests/test_secret_dump_guard.py

Regression tests for .claude/hooks/secret_dump_guard.py (issue #188).

Invokes the hook the same way the Claude Code hook harness does: a JSON
tool_input on stdin, a deny decision (or silence) on stdout. This is
PreToolUse prevention of known dump-command shapes against protected
paths (private_vault/, .env) -- not redaction, and not a guarantee. See
the hook's own module docstring for why.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK_PATH = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "secret_dump_guard.py"


def _run_hook(command: str) -> dict | None:
    """Run the hook with a synthetic Bash tool_input, return the parsed
    decision dict, or None if the hook produced no output (allow)."""
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": command},
    })
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
    )
    stdout = result.stdout.strip()
    if not stdout:
        return None
    return json.loads(stdout)


def _is_denied(command: str) -> bool:
    decision = _run_hook(command)
    if decision is None:
        return False
    return (
        decision.get("hookSpecificOutput", {}).get("permissionDecision")
        == "deny"
    )


class TestDeniesKnownDumpShapes:
    def test_xxd_dot_env(self):
        assert _is_denied("xxd .env")

    def test_cat_dot_env(self):
        assert _is_denied("cat .env")

    def test_the_actual_incident_shape(self):
        """The exact command that produced the real leak this issue is
        filed for: tail piped into xxd, .env as the source."""
        assert _is_denied("tail -c 200 .env | xxd")

    def test_hexdump_private_vault_file(self):
        assert _is_denied("hexdump private_vault/some_file.json")

    def test_get_content_dot_env(self):
        assert _is_denied("Get-Content .env")

    def test_type_dot_env(self):
        assert _is_denied("type .env")

    def test_less_private_vault_path(self):
        # The Bash tool is documented as Git Bash / POSIX sh (forward
        # slashes); a literal backslash in a real command is a shell
        # escape character, not a path separator, for this tool the same
        # way it is for any POSIX shell -- so this uses the tool's actual
        # convention rather than a Windows-native backslash path.
        assert _is_denied("less private_vault/notes.json")

    def test_deny_reason_names_the_utility_and_is_not_silent(self):
        decision = _run_hook("cat .env")
        assert decision is not None
        reason = decision["hookSpecificOutput"]["permissionDecisionReason"]
        assert "cat" in reason
        assert "#188" in reason


class TestAllowsSafePatterns:
    """The explicit regression guard against over-blocking: every one of
    these was used safely, on real .env, earlier in the session that
    filed #188. A filter that denies these trains people to ignore it."""

    def test_grep_variable_names_only(self):
        assert not _is_denied('grep -no "^[A-Z_]*KEY" .env')

    def test_awk_lengths_only(self):
        assert not _is_denied("awk '{print length($0)}' .env")

    def test_append_via_printf(self):
        assert not _is_denied("printf '\\nFOO=bar\\n' >> .env")

    def test_word_count(self):
        assert not _is_denied("wc -l .env")

    def test_list_file(self):
        assert not _is_denied("ls -la .env")

    def test_unrelated_command_no_protected_path(self):
        assert not _is_denied("cat README.md")

    def test_utility_name_as_substring_does_not_false_positive(self):
        """'category'/'heading' etc. must not match the 'cat'/'head'
        word-boundary blocklist."""
        assert not _is_denied("echo 'category heading' > .env")

    def test_heredoc_mentioning_dot_env_in_prose_is_not_flagged(self):
        """The exact false positive this hook produced against its own
        commit: `git commit -m "$(cat <<'EOF' ... .env ... EOF)"` -- 'cat'
        and '.env' both appear in the raw string, but 'cat' is never the
        leading word of a parsed segment (it's inside a quoted
        argument), and '.env' only appears in prose text, not as a path
        token."""
        command = (
            'git commit -m "$(cat <<\'EOF\'\n'
            'fix: mentions .env in the commit body as prose text\n'
            'EOF\n'
            ')"'
        )
        assert not _is_denied(command)

    def test_dump_utility_and_protected_path_in_different_segments(self):
        """cat on an unrelated file, .env only named in a later, separate
        command -- must not cross-contaminate across segments."""
        assert not _is_denied("cat README.md && echo done .env-related-work")


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
