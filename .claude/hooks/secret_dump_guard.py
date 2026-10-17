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

Command forms walked. The command is parsed into pipe stages, and every
stage is reduced to its effective command before any rule runs:
  - leading `NAME=value` words, `sudo`, `env`, `time`, `nice`, `nohup`,
    `command`, `exec`, `builtin` and shell keywords (`if then do { !` ...)
    are skipped;
  - the bodies of `$(...)`, backticks, `<(...)` / `>(...)` (not inside
    single quotes) and the command string of `bash|sh|zsh|dash|ksh|fish|
    csh|tcsh -c`, `pwsh|powershell [-Command]`, `cmd /c|/k|/r`, `eval` and
    `wsl [flags] [--|-e] CMD` are parsed as command lines of their own, to a
    nesting depth of 8 (deeper is denied, with a reason that names the
    limit);
  - the words of a substitution are also operands of the stage that holds
    it, since its output becomes words of that command: `cat $(echo
    private_vault/a.json)` is a read of the vault, though the body only
    echoes;
  - parentheses start a new segment, so `echo (gc x)` exposes `gc`;
  - a backslash before a quote, `$` or a backtick is an escape, as in Bash
    (`echo it\\'s; cat x`). PowerShell reads a backslash as a path separator
    whatever follows, so a command holding such an escape is parsed both
    ways and denied if either reading is.
Name rule. A stage that names a vault path is denied whatever its utility
(`python private_vault/x.py` too); echo/printf/test/[/[[/Test-Path/
Write-Output/Write-Host/mkdir are checked for redirection targets only
(`echo x > private_vault/y`), and cd only moves the base directory. The
pattern operand of a content search is text, not a path, and is exempt
(`rg private_vault src`). A wildcard names a vault only when it carries the
literal text "vault" (`*vault*`); `priv*` is caught by location when a
vault directory exists. The command word counts too: running a file under
a vault (`./private_vault/run.sh`) is denied.
Heredocs. A body is dropped only when its receiver merely stores or
displays it: `cat` (not piped, and not substituted into another command's
words), `tee`, `git ... -F -`, `gh ... --body-file -`, and the `cat` of a
`-m "$(cat <<'EOF' ...)"` message. Any other receiver (a shell, an
interpreter incl. versioned `python3.12`, `sqlite3`, `xargs`, a pipe) keeps
the body scanned. Behind a bare delimiter the `$(...)` in the body is live
and is scanned either way.
Value-taking flags (`rg -C 2`, `rg -g '*.py'`, `gci -Depth 3`) are parsed
so their values are not mistaken for the pattern or a search root. A root
that does not exist on disk adds the cwd as a root (grep/rg family).

.env is NOT handled here. env_guard.py owns .env for every tool (Read,
Grep, Glob, Bash, PowerShell) and denies any command that names a .env
file, whatever the utility. This hook used to substring-match ".env",
which both missed most readers and wrongly denied `cat .env.example`.

This is PREVENTION of known command forms, not redaction, and it is not
a guarantee (issue #188).

Threat model. These guards prevent accidental exposure by an agent: a tool
call that would put vault content or entry names into the transcript by
mistake, in a known form. They are pattern checks, not a sandbox, and do not
defend against deliberate evasion (a path built at runtime, a script or
encoding, a program that opens the file itself).

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
as one -- is not caught: the space of "ways to print a file's content" in a
shell is unbounded, and claiming to catch all of it would be worse than
admitting this only raises the bar for common forms.

Comments. A `#` that starts a word, outside quotes, comments out the rest of
its line, as in both shells; the lines after it are still read.
"""

import json
import os
import re
import shlex
import sys

# Same directory; Python puts it on sys.path when this file runs as a script.
from hook_utils import (
    absolute_path,
    command_name,
    deny,
    deny_on_error,
    drop_inert_heredocs,
    extract_substitutions,
    shell_step,
)

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

# Commands that print or test their operands without reading them: only a
# redirection target on such a stage can name a vault.
_REDIRECT_ONLY = frozenset({
    "echo", "printf", "test", "[", "[[", "test-path", "write-output", "write-host", "mkdir",
})

# Words skipped in command position. Wrappers map to the flags that take a
# value; keywords open a block and run nothing themselves.
_WRAPPERS = {
    "time": frozenset(),
    "nohup": frozenset(),
    "command": frozenset(),
    "exec": frozenset(),
    "builtin": frozenset(),
    "nice": frozenset({"-n"}),
    "sudo": frozenset({"-u", "-g", "-h", "-p", "-C", "-r", "-t", "-T", "-U"}),
    "env": frozenset({"-u", "-C", "-S"}),
}
_KEYWORDS = frozenset({"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "{", "}", "!"})
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\+?=")
_XARGS_VALUE = frozenset({"-n", "-P", "-I", "-L", "-s", "-d", "-E", "-a"})

# Nested command lines (see _nested).
_POSIX_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish", "csh", "tcsh"})
_PS_SHELLS = frozenset({"pwsh", "powershell"})
# pwsh/powershell flags that take the next word as a value.
_PS_SHELL_VALUE = frozenset({
    "executionpolicy", "ep", "windowstyle", "w", "outputformat", "o", "inputformat", "in",
    "configurationname", "config", "workingdirectory", "wd", "settingsfile", "version", "v",
})
# wsl flags that take the next word as a value; `--` and -e/--exec end the flags.
_WSL_VALUE = frozenset({"-d", "--distribution", "-u", "--user", "--cd", "--shell-type"})
_SHELL_C_FLAG = re.compile(r"-[a-zA-Z]*c[a-zA-Z]*")
_MAX_DEPTH = 8


class _NestedTooDeep(str):
    """The match reported when a command nests past _MAX_DEPTH. A type of its
    own: a command word that reads "nested command" is an ordinary match."""


_NESTED = _NestedTooDeep("nested command")

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

# Value-taking flags of the content searchers: the next word is the flag's
# value, not a pattern or a root. (short letters, long names)
_SEARCH_VALUE_FLAGS = {
    "grep": ("ABCmefdD", frozenset({
        "after-context", "before-context", "context", "max-count", "regexp", "file", "directories",
        "devices", "include", "exclude", "exclude-dir", "binary-files", "label",
    })),
    "rg": ("ABCmgtTjefdMEr", frozenset({
        "after-context", "before-context", "context", "max-count", "glob", "iglob", "type", "type-not",
        "type-add", "threads", "regexp", "file", "max-depth", "maxdepth", "replace", "ignore-file",
        "max-filesize", "max-columns", "encoding", "sort", "sortr", "path-separator", "pre", "pre-glob",
        "engine",
    })),
    "ag": ("ABCmgGpW", frozenset({
        "after-context", "before-context", "context", "max-count", "depth", "ignore", "file-search-regex",
        "path-to-ignore", "ignore-dir", "ignore-file", "type",
    })),
}
# PowerShell parameters that take a value, as (name, shortest abbreviation).
_LISTER_VALUES = (("depth", 3), ("include", 3), ("exclude", 2), ("filter", 2))
_SELECT_VALUES = (
    ("pattern", 4), ("path", 4), ("literalpath", 1), ("include", 3), ("exclude", 2), ("context", 2),
    ("encoding", 2),
)
_TREE_VALUE = frozenset({"-L", "-P", "-I", "-o", "-H", "--filelimit", "--charset"})


class _TooDeep(Exception):
    """Command substitution or a nested shell goes deeper than _MAX_DEPTH."""


class _Redir(str):
    """A word that is the target of a redirection (`> x`, `< x`)."""


# ---------------------------------------------------------------------------
# Parsing: heredocs, substitution, pipelines, wrappers, nested shells
# ---------------------------------------------------------------------------

# A substitution marker (hook_utils.extract_substitutions) in a word.
_MARKER = re.compile("\x01(\\d+)\x01")
# Splits a substitution body into loose words (no shlex, so no quoting
# trick can make a body unreadable).
_LOOSE = re.compile("[\\s\"'`;|&<>(){}=,$\x01]+")


def _unescape(command: str, keep_escapes: bool) -> str:
    """`command` with every backslash read as a path separator ("/"), so
    C:\\x\\private_vault\\a.json survives shlex, which treats a backslash as
    an escape. With `keep_escapes` the escapes that matter to quoting
    (backslash before a quote, `$` or a backtick, outside single quotes) stay
    escapes, as in Bash. The two readings differ only for a command holding
    such an escape, and _pipelines walks both: PowerShell reads a backslash
    as a separator whatever follows, Bash does not."""
    if not keep_escapes or "\\" not in command:
        return command.replace("\\", "/")
    out: list[str] = []
    quote, i = "", 0
    while i < len(command):
        size, after = shell_step(command, i, quote)
        piece = command[i : i + size]
        if piece[0] == "\\" and (size == 1 or piece == "\\\\"):
            piece = "/" * size
        out.append(piece)
        quote, i = after, i + size
    return "".join(out)


def _strip_comments(text: str) -> str:
    """`text` without shell comments: a `#` that starts a word, outside
    quotes, up to the end of its line (the newline stays). `a#b`, `$#`,
    `${#x}` and a quoted `#` are not comments."""
    if "#" not in text:
        return text
    out: list[str] = []
    quote, i = "", 0
    while i < len(text):
        size, after = shell_step(text, i, quote)
        piece = text[i : i + size]
        if piece == "#" and not quote and (not out or out[-1][-1] in " \t\n;|&(<>"):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
            continue
        out.append(piece)
        quote, i = after, i + size
    return "".join(out)


def _tokenize(text: str) -> list[list[list[str]]]:
    """Pipelines (separated by ; && || & newline or a parenthesis), each a
    list of pipe stages, each a list of shlex words. Quote-aware: a `|` or
    `;` inside quotes (egrep 'a|b') is part of a word. A command shlex
    cannot cleanly tokenize (unmatched quotes) yields nothing rather than
    falling back to a loose substring check: precision matters more than
    recall here (see module docstring)."""
    lexer = shlex.shlex(_strip_comments(text).replace("\n", " ; "), posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    lexer.commenters = ""  # shlex would end the whole command at any #
    try:
        tokens = list(lexer)
    except ValueError:
        return []
    pipelines: list[list[list[str]]] = [[[]]]
    redirect = False
    for token in tokens:
        if not set(token) <= _PUNCTUATION:
            pipelines[-1][-1].append(_Redir(token) if redirect else token)
            redirect = False
        elif any(c in token for c in "();") or token in ("||", "&&", "&"):
            pipelines.append([[]])
        elif token in ("|", "|&"):
            pipelines[-1].append([])
        elif "<" in token or ">" in token:
            redirect = True
    return pipelines


def _core(tokens: list[str]) -> tuple[list[str], bool]:
    """The stage's effective command and arguments: leading assignments,
    shell keywords and wrappers (sudo, env, time, nice, nohup, ...) are
    skipped, and for `xargs [flags] cmd` the command xargs runs. The flag
    reports an xargs stage, whose operands arrive on stdin."""
    i, n = 0, len(tokens)
    while i < n:
        word = tokens[i]
        name = command_name(word)
        if _ASSIGNMENT.match(word) or name in _KEYWORDS:
            i += 1
        elif name in _WRAPPERS:
            i += 1
            while i < n and tokens[i].startswith("-") and len(tokens[i]) > 1:
                i += 2 if tokens[i] in _WRAPPERS[name] else 1
        else:
            break
    core = tokens[i:]
    if core and command_name(core[0]) == "xargs":
        j = 1
        while j < len(core) and core[j].startswith("-"):
            j += 2 if core[j] in _XARGS_VALUE else 1
        if core[j:]:
            return core[j:], True
    return core, False


def _nested(core: list[str]) -> str | None:
    """The command line a shell stage runs: `bash|fish|csh -c STR`, `pwsh
    [-Command] STR`, `cmd /c|/k|/r STR`, `eval STR`, `wsl [flags] [--|-e] CMD`."""
    name, rest = command_name(core[0]), core[1:]
    if name in _POSIX_SHELLS:
        for k, word in enumerate(rest):
            if _SHELL_C_FLAG.fullmatch(word):
                k += 1
                # Options may follow -c; a lone `--` ends them.
                while k < len(rest) and rest[k][:1] in ("-", "+") and len(rest[k]) > 1:
                    flag = rest[k]
                    k += 1
                    if flag == "--":
                        break
                    k += flag in ("-o", "+o", "-O", "+O")
                return rest[k] if k < len(rest) else None
    elif name == "eval":
        return " ".join(rest) or None
    elif name == "wsl":
        k = 0
        while k < len(rest) and rest[k].startswith("-"):
            flag = rest[k]
            k += 1
            if flag in ("--", "-e", "--exec"):
                break
            k += flag in _WSL_VALUE
        return shlex.join(rest[k:]) or None
    elif name == "cmd":
        for k, word in enumerate(rest):
            if word.lower() in ("/c", "/k", "/r"):
                return " ".join(rest[k + 1 :]) or None
    elif name in _PS_SHELLS:
        k = 0
        while k < len(rest) and rest[k].startswith("-"):
            flag = rest[k][1:].lower().split(":", 1)[0]
            k += 1
            if flag and "command".startswith(flag):
                break
            if flag in _PS_SHELL_VALUE:
                k += 1
            elif flag and ("file".startswith(flag) or "encodedcommand".startswith(flag)):
                return None  # a script or an encoded blob, not a command line
        return " ".join(rest[k:]) or None
    return None


def _substitution_words(bodies: list[str], stage: list[str]) -> list[str]:
    """The loose words of the substitutions whose markers appear in `stage`.
    The output of a substitution becomes words of the stage that holds it
    (`cat $(echo private_vault/a.json)`), so those words are checked as the
    stage's operands as well as the body being checked as a command."""
    words: list[str] = []
    for token in stage:
        for index in _MARKER.findall(token):
            words.extend(w for w in _LOOSE.split(bodies[int(index)]) if w)
    return words


def _parse(text: str, depth: int, heredocs: bool) -> list[list[tuple[list[str], bool]]]:
    """The pipelines of one reading of a command line (see _unescape).
    `heredocs`: decide the heredocs in `text`. A substitution body was
    already covered by the pass over the line that held it, where its
    receiver and surroundings were known."""
    if heredocs:
        text = drop_inert_heredocs(text, literal_only=False)
    text, bodies = extract_substitutions(text)
    outer = []
    for pipeline in _tokenize(text):
        stages = []
        for core, via_xargs in map(_core, pipeline):
            if core:
                stages.append((core + _substitution_words(bodies, core), via_xargs))
        if stages:
            outer.append(stages)
    inner = []
    for body in bodies:
        inner.extend(_pipelines(body, depth + 1, heredocs=False))
    for pipeline in outer:
        for core, _ in pipeline:
            line = _nested(core)
            if line:
                # Markers of the outer line stand for substitutions that sat
                # in this string: put them back for the nested parse.
                line = _MARKER.sub(lambda m: f"$({bodies[int(m[1])]})", line)
                inner.extend(_pipelines(line, depth + 1))
    return outer + inner


def _pipelines(command: str, depth: int = 0, heredocs: bool = True) -> list[list[tuple[list[str], bool]]]:
    """Every pipeline in `command`, including those inside command
    substitutions and nested shell command strings. Each stage is
    `_core`'s (tokens, via_xargs) plus the words of the substitutions it
    holds; empty stages are dropped. A command holding a backslash escape is
    read both ways (_unescape) and the pipelines of both are returned."""
    if depth > _MAX_DEPTH:
        raise _TooDeep
    command = command.replace("\r\n", "\n")
    readings = dict.fromkeys((_unescape(command, False), _unescape(command, True)))
    return [pipeline for text in readings for pipeline in _parse(text, depth, heredocs)]


# ---------------------------------------------------------------------------
# Operands
# ---------------------------------------------------------------------------

def _ps_value_flag(token: str, table) -> str | None:
    """The PowerShell parameter in `table` that `token` abbreviates, or None.
    PowerShell accepts any unambiguous prefix (-Dep, -Incl)."""
    if not token.startswith("-") or token.startswith("--") or len(token) < 2:
        return None
    word = token[1:].split(":", 1)[0].lower()
    return next((name for name, least in table if len(word) >= least and name.startswith(word)), None)


def _search_family(utility: str) -> str | None:
    if utility in _GREP:
        return "grep"
    if utility == "rg":
        return "rg"
    return "ag" if utility in ("ag", "ack") else None


def _parse_search(tokens: list[str], utility: str) -> tuple[list[int], set[int]]:
    """For grep/rg/ag/ack: the indices of the positional words (not flags,
    not flag values) and of the words that are pattern text -- the value of
    -e/--regexp, or the first positional when no flag supplies the pattern.
    A short cluster takes the value of its first value letter: `-A 2`,
    `-rnA 2`, `-A2`."""
    short, long_names = _SEARCH_VALUE_FLAGS[_search_family(utility)]
    positional: list[int] = []
    pattern: set[int] = set()
    explicit = False
    i, n = 1, len(tokens)
    while i < n:
        word = tokens[i]
        i += 1
        if word == "--":
            positional.extend(range(i, n))
            break
        if word.startswith("--"):
            name, eq, _ = word[2:].partition("=")
            if name in ("regexp", "file", "files"):
                explicit = True
            if name == "regexp" and eq:
                pattern.add(i - 1)
            if name in long_names and not eq and i < n:
                if name == "regexp":
                    pattern.add(i)
                i += 1
        elif word.startswith("-") and len(word) > 1:
            for k, letter in enumerate(word[1:], 2):
                if letter not in short:
                    continue
                explicit = explicit or letter in "ef"
                attached = k < len(word)
                if letter == "e":
                    pattern.add(i - 1 if attached else i)
                if not attached and i < n:
                    i += 1
                break
        else:
            positional.append(i - 1)
    if not explicit and positional:
        pattern.add(positional[0])
    return positional, pattern


def _pattern_indices(tokens: list[str], utility: str) -> set[int]:
    """Indices of the words of a content search that are pattern text. A
    pattern is not a path (`rg private_vault src` searches src for a word)."""
    if _search_family(utility):
        return _parse_search(tokens, utility)[1]
    if utility == "findstr":
        found = {i for i, t in enumerate(tokens) if i and t.lower().startswith("/c:")}
        explicit = found or any(t.lower().startswith("/g:") for t in tokens[1:])
        first = next((i for i, t in enumerate(tokens) if i and not t.startswith("-") and not _SWITCH_RE.fullmatch(t)), None)
        return found if explicit or first is None else {first}
    if utility in ("select-string", "sls"):
        pattern: set[int] = set()
        positional: list[int] = []
        explicit = False
        i = 1
        while i < len(tokens):
            word = tokens[i]
            i += 1
            name = _ps_value_flag(word, _SELECT_VALUES)
            if name:
                if name == "pattern":
                    explicit = True
                    pattern.add(i - 1 if ":" in word else i)
                if ":" not in word:
                    i += 1
            elif not word.startswith("-"):
                positional.append(i - 1)
        if not explicit and positional:
            pattern.add(positional[0])
        return pattern
    return set()


def _operands(tokens: list[str], utility: str = "") -> list[str]:
    """Positional arguments: not flags, not flag values, not cmd-style
    switches."""
    out: list[str] = []
    skip = False
    for token in tokens[1:]:
        if skip:
            skip = False
        elif token.startswith("-"):
            if utility == "tree":
                skip = token in _TREE_VALUE
            elif utility in _LISTERS:
                skip = ":" not in token and _ps_value_flag(token, _LISTER_VALUES) is not None
        elif not _SWITCH_RE.fullmatch(token):
            out.append(token)
    return out


def _path_candidates(token: str) -> list[str]:
    """`token`, plus the value of `--flag=value`, `if=value` and PowerShell
    `-Param:value`. Bare flags are not paths."""
    candidates = [] if token.startswith("-") else [token]
    if "=" in token:
        candidates.append(token.split("=", 1)[1])
    if token.startswith("-") and ":" in token:
        candidates.append(token.split(":", 1)[1])
    return [c for c in candidates if c]


def _name_args(tokens: list[str], utility: str, full: bool) -> tuple[list[str], list[str]]:
    """The path candidates and the flag words of a stage, for the name rule.
    A print or test command (echo, Test-Path, ...) contributes only its
    redirection targets unless `full`; a content search's pattern text
    contributes nothing."""
    if utility in _REDIRECT_ONLY and not full:
        return [t for t in tokens[1:] if isinstance(t, _Redir)], []
    skip = _pattern_indices(tokens, utility)
    args = [t for i, t in enumerate(tokens) if i and i not in skip]
    return [c for t in args for c in _path_candidates(t)], [t for t in args if t.startswith("-")]


# ---------------------------------------------------------------------------
# Recursion
# ---------------------------------------------------------------------------

def _recursive_roots(tokens: list[str], utility: str, base: str) -> list[str] | None:
    """Directories a recursive content search or listing in this stage
    walks, or None when the stage does not recurse. Flag values are not
    roots (`rg -C 2 KEY`, `gci -Recurse -Depth 3`). For grep/rg/ag/ack, a
    root that does not exist on disk may be a flag value this parse missed,
    so the cwd is searched as well."""
    flags = [t for t in tokens[1:] if t.startswith("-")]
    if utility in _ALWAYS_RECURSIVE or (
        utility in _GREP and any(_GREP_RECURSE_RE.fullmatch(f) for f in flags)
    ):
        positional, pattern = _parse_search(tokens, utility)
        roots = [tokens[i] for i in positional if i not in pattern] or ["."]
        if any(
            not set("*?[") & set(r) and not os.path.exists(absolute_path(r, base)) for r in roots
        ):
            roots.append(".")
        return roots
    plain = _operands(tokens, utility)
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
    return not _operands(tokens, utility)


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
        roots = _recursive_roots(tokens, utility, base)
        if roots is None:
            continue
        # rg/ag skip the gitignored in-project vault unless told not to.
        honours_gitignore = utility in _GITIGNORE_AWARE and not _ignores_gitignore(tokens)
        reachable = [v for v in vaults if not (v.in_project and honours_gitignore)]
        if any(vg.above_vault(vg.path_forms(root, base), v) for root in roots for v in reachable):
            return True
    return False


def _matches_dump_command(command: str, cwd: str | None = None) -> str | None:
    """Return the matched utility name if a pipeline in `command` names a
    vault path in a stage (print and test commands: in a redirection), or
    runs a content-reading, listing or counting utility that names a vault
    path anywhere in its pipeline or recurses from an ancestor of one, else
    None. Substitutions and nested shells are walked (see module docstring)."""
    base = cwd or os.getcwd()
    try:
        pipelines = _pipelines(command)
    except _TooDeep:
        return _NESTED
    # The name rule needs the literal text "vault" in some word.
    mentions_vault = "vault" in command.lower()
    pending_cd: list[str] = []  # resolved once vault_guard is loaded
    vg = vaults = None
    for pipeline in pipelines:
        utilities = [command_name(core[0]) for core, _ in pipeline]
        if len(pipeline) == 1 and utilities[0] in _CD:
            core = pipeline[0][0]
            pending_cd.append(core[-1] if len(core) > 1 else "~")
            continue
        hit = next((u for u in utilities if u in _READ_UTILITIES or u in _LIST_UTILITIES), None)
        if not hit and not mentions_vault:
            continue
        if vg is None:
            # vault_guard holds the one definition of "a vault path". It is
            # imported only here: most Bash calls run no read or list
            # utility and name no vault, and its imports cost ~15 ms.
            import vault_guard as vg
        # The parsed path candidates (operands, and the value of
        # --flag=value, if=value and -Param:value) feed both rules, so
        # --file=private_vault/x is caught by name with no vault directory.
        # The name rule also reads each whole flag word (-f/x/private_vault).
        # With a reader or lister in the pipeline, every stage counts
        # (`echo private_vault/x | xargs cat`).
        paths: list[str] = []
        for (core, _), utility in zip(pipeline, utilities):
            if utility in _CD and not hit:
                continue
            # Running a file under a vault (./private_vault/run.sh) is a read.
            if vg.names_vault_operand(core[0]):
                return hit or utility
            candidates, flags = _name_args(core, utility, full=bool(hit))
            if any(vg.names_vault_operand(c) for c in (*candidates, *flags)):
                return hit or utility
            paths += candidates
        if not hit:
            continue
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
        direct = [(core, u) for (core, via_xargs), u in zip(pipeline, utilities) if not via_xargs]
        if any(_lists_cwd(core, u) for core, u in direct) and vg.inside_vault(".", base, vaults):
            return hit
        if _reaches_vault(direct, base, vaults, vg):
            return hit
    return None


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    # An unusable payload is allowed through, as an unparseable one is.
    tool_input = data.get("tool_input") if isinstance(data, dict) else None
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not isinstance(command, str) or not command:
        return

    cwd = data.get("cwd")
    try:
        matched_utility = _matches_dump_command(command, cwd if isinstance(cwd, str) else None)
    except Exception as exc:
        # Fail closed: a crash exits non-zero and the harness runs the call.
        deny_on_error("secret_dump_guard", exc)
        return
    if isinstance(matched_utility, _NestedTooDeep):
        deny(
            f"Vault Privacy Rule (#188): this command nests substitutions or "
            f"shells more than {_MAX_DEPTH} levels deep, which the vault "
            "check does not follow, so it is denied rather than allowed "
            "unchecked. Flatten the command. See "
            ".claude/hooks/secret_dump_guard.py."
        )
    elif matched_utility:
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
