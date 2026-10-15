"""
.claude/hooks/secret_dump_guard.py

PreToolUse hook for Bash|PowerShell -- denies commands that print
private_vault/ content through a known whole-file-dump or content-search
utility (cat, gc, grep -r, rg, findstr /s, Select-String -Recurse, ...),
including when the vault is named in one pipe stage and read in another.
Reads tool_input JSON from stdin. Returns a deny decision if the command
matches; silent pass-through otherwise. Read/Grep/Glob are vault_guard.py.

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
output will actually be. So this hook recognizes the "dump or search a
named vault path" family and nothing else. A command written a
different way -- a Python one-liner, a different utility, base64, a
script, `cd private_vault && cat x`, a recursive search from an ancestor
directory that never names the vault (`grep -r x .`) -- is not caught. That is a real, permanent limitation, not an
oversight: the space of "ways to print a file's content" in a shell is
unbounded, and claiming to catch all of it would be worse than admitting
this only raises the bar for common forms.

Precision over recall, deliberately: only content-reading utilities are
blocklisted, not every command that mentions a vault path. Listing,
counting and existence checks are left alone. A filter broad enough to
flag those trains people to ignore it, which is worse than not having
the filter.
"""

import json
import re
import shlex
import sys

# Same directory; Python puts it on sys.path when this file runs as a
# script. One definition of "a vault path" for every vault hook.
from vault_guard import names_vault_path

# Content-reading utilities, lowercase (PowerShell and Windows executables
# are case-insensitive): whole-file dumps, plus content searches, which
# print matching lines and recurse with -r / /s / -Recurse. Deliberately
# narrow -- see module docstring.
_READ_UTILITIES = frozenset({
    # whole-file dumps
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
    # content searches
    "grep",
    "egrep",
    "fgrep",
    "rg",
    "ag",
    "ack",
    "findstr",
    "select-string",
    "sls",
})

# Windows executable suffixes stripped from the leading word, so
# more.com / cat.exe match the bare utility name.
_EXE_SUFFIX_RE = re.compile(r"\.(exe|com)$")

# shlex punctuation runs that are redirections or grouping, not words.
_PUNCTUATION = frozenset("();<>|&")


def _pipelines(command: str) -> list[list[list[str]]]:
    """Split a command into pipelines (separated by ; && || & or newline),
    each a list of pipe stages, each a list of shlex tokens.

    Quote-aware throughout: a `|` or `;` inside quotes (egrep 'a|b') is
    part of a word, and a utility word appearing only INSIDE a quoted
    string (e.g. "cat" inside a "$(cat <<'EOF' ...)" commit message) is
    not a command name. Backslashes are normalised to "/" first: shlex's
    POSIX mode treats a backslash as an escape and would turn
    C:\\x\\private_vault\\a.json into C:xprivate_vaulta.json. A command
    shlex cannot cleanly tokenize (unmatched quotes, heredoc syntax, etc.)
    yields nothing rather than falling back to a loose substring check:
    precision matters more than recall here (see module docstring).
    Redirections and parentheses are dropped; subshells and command
    substitution are not parsed.
    """
    lexer = shlex.shlex(
        command.replace("\\", "/").replace("\n", " ; "), posix=True, punctuation_chars=True
    )
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    pipelines: list[list[list[str]]] = [[[]]]
    for token in tokens:
        if token == "|":
            pipelines[-1].append([])
        elif token in ("||", "&&", "&") or ";" in token:
            pipelines.append([[]])
        elif not set(token) <= _PUNCTUATION:
            pipelines[-1][-1].append(token)
    return pipelines


def _utility(tokens: list[str]) -> str:
    """The stage's command reduced to a bare lowercase name, so an
    absolute-path utility (C:/Windows/System32/more.com, /usr/bin/cat)
    matches. For `xargs [flags] cmd`, the command xargs runs."""
    name = _EXE_SUFFIX_RE.sub("", tokens[0].rsplit("/", 1)[-1].lower())
    if name == "xargs":
        return next((_utility([t]) for t in tokens[1:] if not t.startswith("-")), name)
    return name


def _matches_dump_command(command: str) -> str | None:
    """Return the matched utility name if a pipeline in `command` both runs
    a content-reading utility and names a vault path, else None."""
    for pipeline in _pipelines(command):
        stages = [tokens for tokens in pipeline if tokens]
        utilities = [_utility(tokens) for tokens in stages]
        names_vault = any(names_vault_path(t) for tokens in stages for t in tokens[1:])
        if names_vault:
            hit = next((u for u in utilities if u in _READ_UTILITIES), None)
            if hit:
                return hit
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
