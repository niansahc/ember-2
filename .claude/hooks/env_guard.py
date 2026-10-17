"""
.claude/hooks/env_guard.py

PreToolUse hook for Read|Grep|Glob|Edit|Write|Bash|PowerShell -- denies
agent tool calls that name a .env file (.env, .env.local,
.env.production, ...). The exact name .env.example is allowed everywhere;
a glob that can also match .env.example.<x> (.env.example*) is not.

Why: .env holds live credentials, and anything a tool reads lands in the
session transcript. A transcript is not a secret store. This hook refuses
the call before it runs.

Port of ember-2-ui .claude/hooks/block-env-reads.cjs (PR #64): basename
match after backslash normalisation, glob rule, input fields per tool,
prose skip scoped to git/gh message flags. Additions: Edit/Write, Win32
name aliases, and ripgrep glob semantics for Grep.

Globs. A dot-led glob is denied when it can match `.env` or some
`.env.<x>` (`.e*`, `.en?`, `.[e]nv`, `.*`); one that cannot (`.[0]`,
`.e[x]`) is not a .env name and is allowed.

Quoting (Bash only). Bash does not expand wildcards inside quotes, so a
quoted dot-led pattern with no literal text after the dot (`'.*'`,
`'.[0]'`, `".*KEY"`) is passed to the program as text: jq paths and
regexes, not file names. Such a word is allowed, except:
  - after a flag whose value the program matches against file names
    (find -name, rg -g/--glob, grep --include), and
  - in a double-quoted span holding command substitution ($(...) or
    backticks), or anywhere in a command that runs a nested shell
    (bash -c, sh -c, eval, pwsh, ...), where the shell does expand it.
A quoted literal name (`'.env'`) or a quoted glob with literal text after
the dot (`'.e*'`) is still denied. PowerShell expands wildcards in quoted
cmdlet arguments, so quoting changes nothing there.

Quoting is read by the scanner shared with secret_dump_guard.py
(hook_utils.shell_spans): a backslash before a quote is an escape, so in
`cat \\' .* \\'` the glob is unquoted and expanded by the shell.

Heredocs. The body of a heredoc behind a quoted delimiter is dropped only
when its receiver merely stores or displays it (`cat` not piped and not
substituted into another command's words, `tee`, `git ... -F -`,
`gh ... --body-file -`, and the `cat` of a `-m "$(cat <<'EOF' ...)"`
message). Any other receiver (a shell, an interpreter incl. `python3.12`,
`sqlite3`, `xargs`, a pipe) keeps it scanned, and so does a bare-delimiter
body.

Threat model. These guards prevent accidental exposure by an agent: a tool
call that would put a .env file's contents into the transcript by mistake, in a known form.
They are pattern checks, not a sandbox, and do not defend against deliberate
evasion (a path built at runtime, a script or encoding, a program that opens
the file itself).

Limits: a pattern check on the call, not a sandbox. A name built at
runtime (`.e""nv`, PowerShell backtick escapes, base64, a script that
opens the file, an 8.3 short name) is not caught, and neither is a
program that expands a quoted dot-only pattern itself (a Python glob call
on '.*'), or a recursive read that never names the file (grep -r KEY .).

Input: hook JSON on stdin. Output: a deny decision on stdout, or nothing.
Exit code is always 0. An unparseable payload is allowed through with a
note on stderr: failing closed here would block every Read and Bash call
and lock the session out of fixing the hook.
"""

import fnmatch
import json
import re
import sys

# Same directory; Python puts it on sys.path when this file runs as a script.
from hook_utils import command_name, deny, drop_inert_heredocs, shell_spans

# `.env`, `.env.local`, `.env.production`, ... (case-insensitive: Windows).
_ENV_NAME = re.compile(r"^\.env(\..+)?$", re.IGNORECASE)
_EXAMPLE = ".env.example"
_WILDCARD = re.compile(r"[*?[]")
_BRACE = re.compile(r"^(.*)\{([^}]*)\}(.*)$")


def _basename(path: str) -> str:
    trimmed = path.replace("\\", "/").rstrip("/")
    return trimmed.rsplit("/", 1)[-1]


def _canonical_names(path: str) -> set[str]:
    """The names Win32 could resolve a path's last component to: the
    basename, and each colon-separated part of it (`.env::$DATA` stream
    syntax, `C:.env` drive-relative syntax), with trailing dots and spaces
    dropped (`.env.` opens `.env`)."""
    base = _basename(path)
    return {part.rstrip(". ") for part in (base, *base.split(":"))}


def _expand_braces(pattern: str) -> list[str]:
    brace = _BRACE.match(pattern)
    if not brace:
        return [pattern]
    head, alts, tail = brace.groups()
    return [p for alt in alts.split(",") for p in _expand_braces(f"{head}{alt}{tail}")]


# ---------------------------------------------------------------------------
# Can a glob match a .env name?
# ---------------------------------------------------------------------------

def _glob_units(pattern: str) -> list[str]:
    """A glob split into units: "*", or a one-character fnmatch pattern (a
    literal, `?`, or a `[...]` set). An unclosed `[` is a literal, as in
    fnmatch."""
    units: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        if pattern[i] == "[":
            j = i + 1
            if j < n and pattern[j] == "!":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            end = pattern.find("]", j)
            if end >= 0:
                units.append(pattern[i : end + 1])
                i = end + 1
                continue
        units.append(pattern[i])
        i += 1
    return units


def _closure(states: set[int], units: list[str]) -> set[int]:
    """Add the states reached by letting a star, or a `?`, match nothing.
    A Win32 wildcard `?` can match zero characters (cmd `type .env?` opens
    .env), so `?` is given that reading too: it only ever widens what
    counts as reaching a .env name."""
    out = set(states)
    for state in states:
        while state < len(units) and units[state] in ("*", "?"):
            state += 1
            out.add(state)
    return out


def _step(states: set[int], units: list[str], ch: str) -> set[int]:
    """The states after consuming one character."""
    nxt = set()
    for state in states:
        if state >= len(units):
            continue
        unit = units[state]
        if unit == "*":
            nxt.add(state)
        # A literal or `?` is a string compare; only a `[...]` set needs fnmatch.
        elif unit in ("?", ch) or (len(unit) > 1 and fnmatch.fnmatchcase(ch, unit)):
            nxt.add(state + 1)
    return _closure(nxt, units)


def _glob_can_name_env(pattern: str) -> bool:
    """True when a lowercase glob can match `.env` or some `.env.<x>` with a
    non-empty <x>. Runs the glob over `.env`: matched to the end, it names
    `.env`; then over `.`: any unit left can match a non-empty <x>."""
    units = _glob_units(pattern)
    states = _closure({0}, units)
    for ch in ".env":
        states = _step(states, units, ch)
    if len(units) in states:
        return True
    return any(state < len(units) for state in _step(states, units, "."))


def _base_names_env(base: str) -> bool:
    """True when a single basename (no separators) names a .env file."""
    lower = base.lower()
    wild = _WILDCARD.search(base)
    # Only the example file itself. A glob starting with it (`.env.example*`)
    # also matches `.env.example.local`, so it goes through the glob rule.
    if lower == _EXAMPLE:
        return False
    if _ENV_NAME.match(base):
        return True

    # Globs. Only a pattern that starts with a literal dot can match a
    # dotfile (`*` alone does not match `.env`). `.e*`, `.en?`, `.[e]nv`
    # and `.*` can reach a .env file; `.git*` and `.[0]` cannot.
    if wild and base.startswith("."):
        return _glob_can_name_env(lower)

    # Brace sets: `.env{,.local}` / `{.env,.env.local}`.
    alternatives = _expand_braces(base)
    return len(alternatives) > 1 and any(_base_names_env(alt) for alt in alternatives)


def names_env_file(candidate, as_text: bool = False) -> bool:
    """True when `candidate` (a path, filename, or glob) names a protected
    .env file. The exact name .env.example is allowed.
    With `as_text`, a dot followed straight by a wildcard (`.*`, `.[0]`) is
    text, not a file name (see module docstring, Quoting)."""
    if not isinstance(candidate, str):
        return False
    return any(
        _base_names_env(name)
        for name in _canonical_names(candidate)
        if not (as_text and name[:1] == "." and name[1:2] in ("*", "?", "["))
    )


# Grep's `glob` is a ripgrep glob, and ripgrep differs from the dotfile rule
# above in two ways: `*` DOES match a leading dot, and a matching -g glob
# overrides .gitignore. So `glob: "*"` or `"*.local"` makes Grep print .env
# contents (verified live). A Grep glob is therefore also tested against
# representative .env names. A negated glob (`!*.py`) adds no whitelist, so
# .gitignore still applies and it is left alone.
_GREP_GLOB_PROBES = (".env", ".env.local", ".env.development", ".env.production", ".env.test")


def grep_glob_reaches_env(glob) -> bool:
    """True when a Grep (ripgrep) glob could select a .env file."""
    if names_env_file(glob):
        return True
    if not isinstance(glob, str) or glob.startswith("!"):
        return False
    return any(
        fnmatch.fnmatchcase(probe, alt)
        for alt in _expand_braces(_basename(glob).lower())
        for probe in _GREP_GLOB_PROBES
    )


# Which input fields of each non-shell tool can name a file, and the check
# for each. Grep's `pattern` is a content regex, not a path.
PATH_FIELDS = {
    "Read": {"file_path": names_env_file},
    "Edit": {"file_path": names_env_file},
    "Write": {"file_path": names_env_file},
    "Grep": {"path": names_env_file, "glob": grep_glob_reaches_env},
    "Glob": {"path": names_env_file, "pattern": names_env_file},
}
SHELL_TOOLS = frozenset({"Bash", "PowerShell"})


# ---------------------------------------------------------------------------
# Shell commands
# ---------------------------------------------------------------------------

# The quoted value of a message flag on a git or gh command is prose, not a
# path. Scoped to git/gh segments so `cmd -m ".env"` elsewhere is still
# scanned, and only the quoted value right after the flag is dropped. A
# single-quoted value is literal. A double-quoted value is prose only when
# it holds no `$(` and no backtick: the shell runs those, so
# `--body "$(cat .env)"` is scanned like any other command.
_PROSE_FLAG_VALUE = re.compile(
    r"""(?:^|\s)(?:-a?m|--message|--body|--title|--notes)(?:\s+|=)"""
    r"""(?:"(?:[^"\\$`]|\\.|\$(?!\())*"|'[^']*')"""
)
_SEGMENT_SPLIT = re.compile(r"([;&|]+)")
_GIT_OR_GH = re.compile(r"^\s*(?:git|gh)\s")
# Quotes, separators, redirections and `=` (for --path=.env) all delimit.
# Backslashes do NOT, so Windows paths survive intact.
_TOKEN_SPLIT = re.compile(r"""[\s"'`;|&<>(){}=,]+""")
# Programs that run quoted text as a command line of their own, where the
# wildcards in it are expanded after all.
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish", "csh", "tcsh", "eval", "pwsh", "powershell", "cmd", "wsl"})
# Flags whose value the program matches as a glob against file names.
_GLOB_FLAGS = frozenset({
    "-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename",
    "-g", "--glob", "--iglob", "--include",
})


def _without_prose(command: str) -> str:
    command = drop_inert_heredocs(command)
    return "".join(
        _PROSE_FLAG_VALUE.sub(" ", seg) if _GIT_OR_GH.match(seg) else seg
        for seg in _SEGMENT_SPLIT.split(command)
    )


def _words(command: str, bash: bool) -> list[tuple[str, bool]]:
    """The command's words, each with `quoted`: True when, in a Bash
    command that runs no nested shell, the shell passes the word's
    wildcards through as text."""
    spans = [
        (span, [word for word in _TOKEN_SPLIT.split(span) if word])
        for span in shell_spans(_without_prose(command))
    ]
    if bash and any(command_name(word) in _SHELLS for _, words in spans for word in words):
        bash = False
    return [
        (word, bash and len(span) > 1 and span[0] in "\"'"
         and not (span[0] == '"' and ("$(" in span or "`" in span)))
        for span, words in spans
        for word in words
    ]


def command_names_env_file(command, bash: bool = False) -> str | None:
    """Return the first word of a shell command that names a .env file.
    `bash`: apply Bash quoting (see module docstring)."""
    if not isinstance(command, str):
        return None
    previous = ""
    for word, quoted in _words(command, bash):
        if names_env_file(word, as_text=quoted and previous not in _GLOB_FLAGS):
            return word
        previous = word
    return None


def evaluate(payload) -> str | None:
    """Return the matched target when the tool call names a .env file, else None."""
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    if tool in SHELL_TOOLS:
        return command_names_env_file(tool_input.get("command"), bash=tool == "Bash")
    for field, names_env in PATH_FIELDS.get(tool, {}).items():
        value = tool_input.get(field)
        if names_env(value):
            return value
    return None


def reason(target: str) -> str:
    return (
        f"Blocked by project hook: .env files may not be read or edited "
        f"(matched '{target}'). .env holds live credentials and anything "
        f"read lands in the session transcript. Use .env.example for "
        f"variable names, or ask the user to supply a value. "
        f"See .claude/hooks/env_guard.py."
    )


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        print(f"[env_guard] unparseable hook payload, allowing: {exc}", file=sys.stderr)
        return
    target = evaluate(payload)
    if target:
        deny(reason(target))


if __name__ == "__main__":
    main()
