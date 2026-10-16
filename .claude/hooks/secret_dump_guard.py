"""
.claude/hooks/secret_dump_guard.py

PreToolUse hook for Bash|PowerShell -- denies commands that print vault
content through a known whole-file-dump or content-search utility (cat,
gc, grep -r, rg, findstr /s, Select-String -Recurse, ...), or that list or
count vault entries (ls, dir, gci, tree, find, wc), including when the
vault is named in one pipe stage and read in another. Entry names are
record IDs, so a listing is a read. Reads hook JSON from stdin. Returns a
deny decision if the command matches; silent pass-through otherwise.
Read/Grep/Glob are vault_guard.py.

"The vault" is vault_guard.py's definition (by name and by location). A
path argument counts when it resolves, against the payload cwd and any
`cd` earlier in the same command line, to a location at or under a vault,
or names a private_vault directory. Flag values (--file=x, if=x,
-Path:x) count as path arguments for both rules.
A recursive search or listing (grep -r, rg, findstr /s, gci -Recurse,
find, tree, ls -R, ...) counts when its root is an ancestor of one,
unless the utility honours
.gitignore (rg, ag) and the vault is the gitignored in-project one. A
non-recursive listing of an ancestor shows only the vault directory's own
name and is allowed.

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
output will actually be. So this hook recognizes the "dump, search, or
list a vault path" family and nothing else. A command written a
different way -- a Python one-liner, a different utility, base64, a script, a path
built at runtime ($VAR, %VAR%), a flag value taken for a path or missed
as one -- is not caught. That is a real, permanent limitation, not an
oversight: the space of "ways to print a file's content" in a shell is
unbounded, and claiming to catch all of it would be worse than admitting
this only raises the bar for common forms.

Precision over recall, deliberately: only content-reading, listing and
counting utilities are blocklisted, not every command that mentions a
vault path. Existence checks (Test-Path, test -d) and other commands that
merely name a vault (echo, mkdir) are left alone. A filter broad enough to
flag those trains people to ignore it, which is worse than not having
the filter.
"""

import json
import os
import re
import shlex
import sys

# Same directory; Python puts it on sys.path when this file runs as a script.
from hook_utils import absolute_path, command_name, deny

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

# Listing and counting utilities: their output is vault entry names (record
# IDs) or a count of them. Denied when they name a vault, or when they
# recurse from an ancestor of one (see _recursive_roots).
_LIST_UTILITIES = frozenset({
    "ls",
    "dir",
    "gci",
    "get-childitem",
    "tree",
    "find",
    "wc",
})

# shlex punctuation runs that are redirections or grouping, not words.
_PUNCTUATION = frozenset("();<>|&")

# Directory changes tracked across the pipelines of one command line.
_CD = frozenset({"cd", "chdir", "pushd", "set-location", "sl", "push-location"})

# Recursion, per utility family. Content searches that always recurse:
_ALWAYS_RECURSIVE = frozenset({"rg", "ag", "ack"})
# ...and those that honour .gitignore unless told not to (-u, --no-ignore).
_GITIGNORE_AWARE = frozenset({"rg", "ag"})
_GREP = frozenset({"grep", "egrep", "fgrep"})
_LISTERS = frozenset({"get-childitem", "gci", "ls", "dir"})
# PowerShell accepts any unambiguous prefix of -Recurse.
_PS_RECURSE = frozenset({"-r", "-re", "-rec", "-recu", "-recur", "-recurs", "-recurse", "--recursive"})
_GREP_RECURSE_RE = re.compile(r"-[a-zA-Z]*[rR][a-zA-Z]*|--(?:dereference-)?recursive|--directories=recurse")
# cmd-style switches (/s, /i, /c:text); a path like /c/x has a second slash.
_SWITCH_RE = re.compile(r"/[a-z]+(?::.*)?", re.IGNORECASE)
_PATTERN_FLAGS = ("-e", "-f", "--regexp", "--file")


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
    """The stage's command name; for `xargs [flags] cmd`, the command xargs
    runs."""
    name = command_name(tokens[0])
    if name == "xargs":
        return next((command_name(t) for t in tokens[1:] if not t.startswith("-")), name)
    return name


def _operands(tokens: list[str]) -> list[str]:
    """Positional arguments: not flags, not cmd-style switches."""
    return [t for t in tokens[1:] if not t.startswith("-") and not _SWITCH_RE.fullmatch(t)]


def _path_candidates(token: str) -> list[str]:
    """`token`, plus the value of `--flag=value`, `if=value` and PowerShell
    `-Param:value`. Bare flags are not paths."""
    candidates = [] if token.startswith("-") else [token]
    if "=" in token:
        candidates.append(token.split("=", 1)[1])
    if token.startswith("-") and ":" in token:
        candidates.append(token.split(":", 1)[1])
    return [c for c in candidates if c]


def _recursive_roots(tokens: list[str], utility: str) -> list[str] | None:
    """Directories a recursive content search or listing in this stage
    walks, or None when the stage does not recurse. Flag values are taken
    for positionals (`-g '*.py'`); that only adds roots that are rarely
    ancestors of a vault."""
    flags = [t for t in tokens[1:] if t.startswith("-")]
    args = [t for t in tokens[1:] if not t.startswith("-")]
    plain = _operands(tokens)
    if utility in _ALWAYS_RECURSIVE or (
        utility in _GREP and any(_GREP_RECURSE_RE.fullmatch(f) for f in flags)
    ):
        explicit_pattern = any(f in _PATTERN_FLAGS or f.startswith(("--regexp=", "--file=")) for f in flags)
        return (args if explicit_pattern else args[1:]) or ["."]
    if utility == "tree":
        return plain or ["."]
    if utility == "find":
        roots = []
        for t in tokens[1:]:
            if t.startswith(("-", "(", "!")):
                break
            roots.append(t)
        return roots or ["."]
    switches = [t.lower() for t in tokens[1:] if _SWITCH_RE.fullmatch(t)]
    if utility == "findstr":
        if not any("s" in s.split(":")[0] for s in switches):
            return None
        specs = plain if any(s.startswith(("/c:", "/g:")) for s in switches) else plain[1:]
        return [os.path.dirname(s) or "." for s in specs] or ["."]
    if utility in _LISTERS:
        recurses = (
            any(f.lower() in _PS_RECURSE or (not f.startswith("--") and "R" in f) for f in flags)
            or "/s" in switches
        )
        if not recurses:
            return None
        return [(os.path.dirname(a) if set("*?[") & set(a) else a) or "." for a in plain] or ["."]
    return None


def _lists_cwd(tokens: list[str], utility: str) -> bool:
    """A listing with no path argument lists the cwd (`cd vault && ls`)."""
    if utility not in _LIST_UTILITIES or utility == "wc":
        return False  # wc with no file counts stdin
    return not _operands(tokens)


def _ignores_gitignore(tokens: list[str]) -> bool:
    """`-u` (in any short-flag cluster), `--no-ignore*` or `--unrestricted`
    among the stage's flags."""
    return any(
        f.startswith("--no-ignore") or f == "--unrestricted" or (not f.startswith("--") and "u" in f[1:].lower())
        for f in tokens[1:]
        if f.startswith("-")
    )


def _reaches_vault(direct: list[tuple[list[str], str]], base: str, vaults, vg) -> bool:
    """A recursive stage rooted at an ancestor of a vault. `vg` is the
    loaded vault_guard module."""
    for tokens, utility in direct:
        roots = _recursive_roots(tokens, utility)
        if roots is None:
            continue
        # rg/ag skip the gitignored in-project vault unless told not to.
        honours_gitignore = utility in _GITIGNORE_AWARE and not _ignores_gitignore(tokens)
        reachable = [v for v in vaults if not (v.in_project and honours_gitignore)]
        if any(vg.above_vault(vg.path_forms(root, base), v) for root in roots for v in reachable):
            return True
    return False


def _matches_dump_command(command: str, cwd: str | None = None) -> str | None:
    """Return the matched utility name if a pipeline in `command` both runs
    a content-reading, listing or counting utility and names a vault path
    or recurses from an ancestor of one, else None."""
    base = cwd or os.getcwd()
    pending_cd: list[str] = []  # resolved once vault_guard is loaded
    vg = vaults = None
    for pipeline in _pipelines(command):
        stages = [tokens for tokens in pipeline if tokens]
        if not stages:
            continue
        utilities = [_utility(tokens) for tokens in stages]
        if len(stages) == 1 and utilities[0] in _CD:
            pending_cd.append(stages[0][-1] if len(stages[0]) > 1 else "~")
            continue
        hit = next((u for u in utilities if u in _READ_UTILITIES or u in _LIST_UTILITIES), None)
        if not hit:
            continue
        if vg is None:
            # vault_guard holds the one definition of "a vault path". It is
            # imported only here: most Bash calls run no read or list
            # utility, and its imports cost ~15 ms on every call.
            import vault_guard as vg
        # The parsed path candidates (operands, and the value of
        # --flag=value, if=value and -Param:value) feed both rules, so
        # --file=private_vault/x is caught by name with no vault directory.
        # The name rule also reads each whole flag token (-f/x/private_vault).
        args = [t for tokens in stages for t in tokens[1:]]
        paths = [c for t in args for c in _path_candidates(t)]
        flags = [t for t in args if t.startswith("-")]
        if any(vg.names_vault_path(c) for c in (*paths, *flags)):
            return hit
        if vaults is None:
            vaults = vg.vault_dirs()  # reads .env (or its cache)
        if not vaults:
            continue
        for target in pending_cd:
            base = absolute_path(target, base)
        pending_cd.clear()
        if any(vg.inside_vault(c, base, vaults) for c in paths):
            return hit
        # Stages that run their own operands; an xargs stage gets them on stdin.
        direct = [(tokens, u) for tokens, u in zip(stages, utilities) if command_name(tokens[0]) == u]
        if any(_lists_cwd(tokens, u) for tokens, u in direct) and vg.inside_vault(".", base, vaults):
            return hit
        if _reaches_vault(direct, base, vaults, vg):
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

    cwd = data.get("cwd")
    matched_utility = _matches_dump_command(command, cwd if isinstance(cwd, str) else None)
    if matched_utility:
        deny(
            f"Vault Privacy Rule (#188): '{matched_utility}' against a "
            "vault path (private_vault/ or the configured "
            "PRIVATE_VAULT_PATH, named or reached by a recursive "
            "search or listing from an ancestor) would put vault "
            "content or entry names (record IDs) into this "
            "transcript. This is a known-form prevention check, "
            "not a guarantee -- see .claude/hooks/secret_dump_guard.py. "
            "Inspect vault data in a separate terminal; existence "
            "checks (Test-Path, test -d) are allowed."
        )


if __name__ == "__main__":
    main()
