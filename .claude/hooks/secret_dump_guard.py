"""
.claude/hooks/secret_dump_guard.py

PreToolUse hook for Bash -- denies commands that dump a protected file's
raw content (private_vault/*, .env) through a known whole-file-dump
utility. Reads tool_input JSON from stdin. Returns a deny decision if the
command matches; silent pass-through otherwise.

This is PREVENTION of known command shapes, not redaction, and it is not
a guarantee (issue #188).

Claude Code's hook system has no mechanism to redact or replace tool
output after a command runs: PostToolUse hooks receive tool_response but
their only return-value fields are systemMessage/additionalContext/
terminalSequence -- there is no field to modify what the model already
saw, and the docs state plainly that "PostToolUse hooks can't undo
actions since the tool has already executed." PreToolUse is the only
stage with any leverage, and it can only pattern-match the command
string about to run -- it has no visibility into what that command's
output will actually be. So this hook can recognize known shapes (the
two real incidents behind #188: `tail -c 200 .env | xxd`, and the
general "cat/type/xxd/hexdump a protected file" family) and nothing
else. A command written a different way -- a Python one-liner, a
different utility, base64, a script -- is not caught. That is a real,
permanent limitation, not an oversight: the space of "ways to print a
file's content" in a shell is unbounded, and claiming to catch all of it
would be worse than admitting this only raises the bar for common shapes.

Precision over recall, deliberately: only whole-file-dump utilities are
blocklisted, not every command that mentions a protected path. Reading
variable NAMES only (`grep -no "^[A-Z_]*KEY" .env`), computing lengths
(`awk '{print length($0)}' .env`), appending (`printf ... >> .env`),
checking existence/size (`wc -l .env`, `ls -la .env`) are all left
alone -- all were used safely in the session that filed this issue. A
filter broad enough to flag those trains people to ignore it, which is
worse than not having the filter.

The structural fix for the .env half of #188 is issue #189 (move
ANTHROPIC_API_KEY to the keyring, matching EMBER_API_KEY) -- a secret
that is never in a plaintext file can't be dumped by any command, known
or unknown-shaped. This hook does not replace that; it reduces risk for
the interim.
"""

import json
import re
import shlex
import sys

# Same path check as vault_guard.py -- one definition of "protected."
# Applied per shlex TOKEN (an argument word), not the raw command string,
# so ".env" appearing only as a substring of unrelated prose text can't
# match -- see _matches_dump_command.
def _is_protected_path(token: str) -> bool:
    return (
        "private_vault/" in token
        or "private_vault\\" in token
        or ".env" in token
    )


# Whole-file-dump utilities. Deliberately narrow -- see module docstring.
_DUMP_UTILITIES = frozenset({
    "cat",
    "type",
    "more",
    "less",
    "head",
    "tail",
    "xxd",
    "hexdump",
    "od",
    "strings",
    "Get-Content",
})

# Splits a compound command into simple-command segments on shell control
# operators (pipe, and/or, sequence). Does not attempt to parse subshells,
# heredocs, or command substitution -- see module docstring on scope.
_SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|\||;")


def _matches_dump_command(command: str) -> str | None:
    """Return the matched utility name if `command` contains a simple
    segment whose leading word is a dump utility and whose arguments
    include a protected-path-looking token, else None.

    Tokenizes with shlex (quote-aware) per segment so a dump-utility word
    or ".env" substring appearing only INSIDE a quoted string (e.g. a
    heredoc building a multi-line commit message that happens to mention
    ".env" in prose, or "cat" inside "$(cat <<'EOF' ...)") is not treated
    as a command name or a path argument -- it never becomes its own
    token in the position that matters. A segment shlex cannot cleanly
    tokenize (unmatched quotes, heredoc syntax, etc.) is skipped rather
    than falling back to a loose substring check: an unparseable segment
    is exactly the shape most likely to produce a false positive, and
    precision matters more than recall here (see module docstring).
    """
    for segment in _SEGMENT_SPLIT_RE.split(command):
        segment = segment.strip()
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if not tokens:
            continue
        leading = tokens[0].rsplit("/", 1)[-1]  # strip a path prefix like /usr/bin/cat
        if leading not in _DUMP_UTILITIES:
            continue
        if any(_is_protected_path(t) for t in tokens[1:]):
            return leading
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        return

    matched_utility = _matches_dump_command(command)
    if matched_utility:
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Vault Privacy Rule (#188): '{matched_utility}' against a "
                    "protected path (private_vault/ or .env) would dump raw "
                    "content, including secrets, into this transcript. This is "
                    "a known-shape prevention check, not a guarantee -- see "
                    ".claude/hooks/secret_dump_guard.py. If this command "
                    "genuinely needs to inspect that file, read variable "
                    "NAMES only (e.g. grep -no \"^[A-Z_]*KEY\" .env), or "
                    "lengths/counts, never raw values."
                ),
            }
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()
