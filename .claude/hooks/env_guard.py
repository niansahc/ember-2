"""
.claude/hooks/env_guard.py

PreToolUse hook for Read|Grep|Glob|Edit|Write|Bash|PowerShell -- denies
agent tool calls that name a .env file (.env, .env.local,
.env.production, ...). .env.example is allowed everywhere.

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
from hook_utils import deny

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
    fnmatch; consecutive stars collapse."""
    units: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        unit = pattern[i]
        if unit == "[":
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
            unit = "[[]"
        elif unit == "*" and units and units[-1] == "*":
            i += 1
            continue
        units.append(unit)
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
        if units[state] == "*":
            nxt.add(state)
        elif fnmatch.fnmatchcase(ch, units[state]):
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
    # The example file itself, or a glob that can only reach it.
    if lower == _EXAMPLE or (wild and lower.startswith(_EXAMPLE)):
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


def names_env_file(candidate) -> bool:
    """True when `candidate` (a path, filename, or glob) names a protected
    .env file. .env.example and globs that can only reach it are allowed."""
    if not isinstance(candidate, str):
        return False
    return any(_base_names_env(name) for name in _canonical_names(candidate))


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
# scanned, and only the quoted value right after the flag is dropped.
_PROSE_FLAG_VALUE = re.compile(
    r"""(?:^|\s)(?:-a?m|--message|--body|--title|--notes)(?:\s+|=)(?:"(?:[^"\\]|\\.)*"|'[^']*')"""
)
_SEGMENT_SPLIT = re.compile(r"([;&|]+)")
_GIT_OR_GH = re.compile(r"^\s*(?:git|gh)\s")
# Quotes, separators, redirections and `=` (for --path=.env) all delimit.
# Backslashes do NOT, so Windows paths survive intact.
_TOKEN_SPLIT = re.compile(r"""[\s"'`;|&<>(){}=,]+""")
# A closed quoted span, a lone unmatched quote, or an unquoted run.
_SPAN = re.compile(r""""(?:[^"\\]|\\.)*"|'[^']*'|["']|[^"']+""")
# Programs that run quoted text as a command line of their own, where the
# wildcards in it are expanded after all.
_SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh", "fish", "eval", "pwsh", "powershell", "cmd", "wsl"})
# Flags whose value the program matches as a glob against file names.
_GLOB_FLAGS = frozenset({
    "-name", "-iname", "-path", "-ipath", "-wholename", "-iwholename",
    "-g", "--glob", "--iglob", "--include",
})


def _without_prose(command: str) -> str:
    return "".join(
        _PROSE_FLAG_VALUE.sub(" ", seg) if _GIT_OR_GH.match(seg) else seg
        for seg in _SEGMENT_SPLIT.split(command)
    )


def _command_name(word: str) -> str:
    """A word reduced to a bare lowercase command name (/bin/bash, bash.exe)."""
    return re.sub(r"\.exe$", "", _basename(word).lower())


def _words(command: str, bash: bool) -> list[tuple[str, bool]]:
    """The command's words, each with `quoted`: True when, in a Bash
    command, the shell passes the word's wildcards through as text."""
    pairs: list[tuple[str, bool]] = []
    for span in _SPAN.findall(_without_prose(command)):
        quoted = (
            bash
            and len(span) > 1
            and span[0] in "\"'"
            and not (span[0] == '"' and ("$(" in span or "`" in span))
        )
        pairs.extend((word, quoted) for word in _TOKEN_SPLIT.split(span) if word)
    if any(_command_name(word) in _SHELLS for word, _ in pairs):
        return [(word, False) for word, _ in pairs]
    return pairs


def _dot_only_glob(name: str) -> bool:
    """A dot followed straight by a wildcard: `.*`, `.[0]`, `.?x`."""
    wild = _WILDCARD.search(name)
    return wild is not None and wild.start() == 1 and name.startswith(".")


def _word_names_env(word: str, as_text: bool) -> bool:
    """names_env_file(word); with `as_text`, a dot-only glob is text, not a
    file name (see module docstring, Quoting)."""
    if not as_text:
        return names_env_file(word)
    return any(_base_names_env(name) for name in _canonical_names(word) if not _dot_only_glob(name))


def command_names_env_file(command, bash: bool = False) -> str | None:
    """Return the first word of a shell command that names a .env file.
    `bash`: apply Bash quoting (see module docstring)."""
    if not isinstance(command, str):
        return None
    previous = ""
    for word, quoted in _words(command, bash):
        if _word_names_env(word, quoted and previous not in _GLOB_FLAGS):
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
