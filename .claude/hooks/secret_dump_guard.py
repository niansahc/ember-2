"""
.claude/hooks/secret_dump_guard.py

PreToolUse hook for Bash|PowerShell -- denies commands that dump a
private_vault/ file's raw content through a known whole-file-dump
utility. Reads tool_input JSON from stdin. Returns a deny decision if the
command matches; silent pass-through otherwise.

.env is NOT handled here. env_guard.py owns .env for every tool (Read,
Grep, Glob, Bash, PowerShell) and denies any command that names a .env
file, whatever the utility. This hook used to substring-match ".env",
which both missed most readers and wrongly denied `cat .env.example`.

This is PREVENTION of known command forms, not redaction, and it is not
a guarantee (issue #188).

Claude Code's hook system has no mechanism to redact or replace tool
output after a command runs: PostToolUse hooks receive tool_response but
their only return-value fields are systemMessage/additionalContext/
terminalSequence -- there is no field to modify what the model already
saw, and the docs state plainly that "PostToolUse hooks can't undo
actions since the tool has already executed." PreToolUse is the only
stage with any leverage, and it can only pattern-match the command
string about to run -- it has no visibility into what that command's
output will actually be. So this hook recognizes the "cat/type/xxd/
hexdump a vault file" family and nothing else. A command written a
different way -- a Python one-liner, a different utility, base64, a
script -- is not caught. That is a real, permanent limitation, not an
oversight: the space of "ways to print a file's content" in a shell is
unbounded, and claiming to catch all of it would be worse than admitting
this only raises the bar for common forms.

Precision over recall, deliberately: only whole-file-dump utilities are
blocklisted, not every command that mentions a vault path. Listing,
counting and existence checks are left alone. A filter broad enough to
flag those trains people to ignore it, which is worse than not having
the filter.
"""

import json
import re
import shlex
import sys


def _is_protected_path(token: str) -> bool:
    # Tokens arrive with backslashes already normalised to "/" (see
    # _matches_dump_command), so one separator form covers Windows too.
    return "private_vault/" in token


# Whole-file-dump utilities, lowercase (PowerShell and Windows executables
# are case-insensitive). Deliberately narrow -- see module docstring.
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
    "get-content",
    "gc",
})

# Windows executable suffixes stripped from the leading word, so
# more.com / cat.exe match the bare utility name.
_EXE_SUFFIX_RE = re.compile(r"\.(exe|com)$")

# Splits a compound command into simple-command segments on shell control
# operators (pipe, and/or, sequence). Does not attempt to parse subshells,
# heredocs, or command substitution -- see module docstring on scope.
_SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|\||;")


def _matches_dump_command(command: str) -> str | None:
    """Return the matched utility name if `command` contains a simple
    segment whose leading word is a dump utility and whose arguments
    include a protected-path-looking token, else None.

    Backslashes are normalised to "/" before tokenizing: shlex's POSIX
    mode treats a backslash as an escape and would turn
    C:\\x\\private_vault\\a.json into C:xprivate_vaulta.json. The leading
    word is then reduced to its basename, so an absolute-path utility
    (C:\\Windows\\System32\\more.com, /usr/bin/cat) matches by name.

    Tokenizes with shlex (quote-aware) per segment so a dump-utility word
    appearing only INSIDE a quoted string (e.g. "cat" inside a
    "$(cat <<'EOF' ...)" commit message) is not treated as a command
    name. A segment shlex cannot cleanly tokenize (unmatched quotes,
    heredoc syntax, etc.) is skipped rather than falling back to a loose
    substring check: precision matters more than recall here (see module
    docstring).
    """
    for segment in _SEGMENT_SPLIT_RE.split(command):
        segment = segment.strip().replace("\\", "/")
        if not segment:
            continue
        try:
            tokens = shlex.split(segment)
        except ValueError:
            continue
        if not tokens:
            continue
        leading = _EXE_SUFFIX_RE.sub("", tokens[0].rsplit("/", 1)[-1].lower())
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
                    "private_vault/ path would dump raw vault content into "
                    "this transcript. This is a known-form prevention check, "
                    "not a guarantee -- see .claude/hooks/secret_dump_guard.py. "
                    "Inspect vault data in a separate terminal, or use "
                    "counts/listings only."
                ),
            }
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()
