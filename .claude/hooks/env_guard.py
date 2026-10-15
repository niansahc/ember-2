"""
.claude/hooks/env_guard.py

PreToolUse hook for Read|Grep|Glob|Edit|Write|Bash|PowerShell -- denies
agent tool calls that name a .env file (.env, .env.local,
.env.production, ...). .env.example is allowed everywhere.

Why: .env holds live credentials, and anything a tool reads lands in the
session transcript. A transcript is not a secret store. This hook refuses
the call before it runs.

Port of ember-2-ui .claude/hooks/block-env-reads.cjs (PR #64): basename
match after backslash normalisation, literal-prefix glob rule, input
fields per tool, prose skip scoped to git/gh message flags. Additions:
Edit/Write, Win32 name aliases, and ripgrep glob semantics for Grep.

Limits: a pattern check on the call, not a sandbox. A name built at
runtime (`.e""nv`, PowerShell backtick escapes, base64, a script that
opens the file, an 8.3 short name) is not caught.

Input: hook JSON on stdin. Output: a deny decision on stdout, or nothing.
Exit code is always 0. An unparseable payload is allowed through with a
note on stderr: failing closed here would block every Read and Bash call
and lock the session out of fixing the hook.
"""

import fnmatch
import json
import re
import sys

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
    # dotfile (`*` alone does not match `.env`). Compare the literal prefix
    # before the first wildcard: `.e*`, `.en?`, `.env*` and `.env.s*` can
    # all reach a .env file, `.git*` cannot.
    if wild and base.startswith("."):
        literal = lower[: wild.start()]
        return literal.startswith(".env") or ".env".startswith(literal)

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


def _without_prose(command: str) -> str:
    return "".join(
        _PROSE_FLAG_VALUE.sub(" ", seg) if _GIT_OR_GH.match(seg) else seg
        for seg in _SEGMENT_SPLIT.split(command)
    )


def command_names_env_file(command) -> str | None:
    """Return the first token of a shell command that names a .env file."""
    if not isinstance(command, str):
        return None
    return next(
        (token for token in _TOKEN_SPLIT.split(_without_prose(command)) if names_env_file(token)),
        None,
    )


def evaluate(payload) -> str | None:
    """Return the matched target when the tool call names a .env file, else None."""
    if not isinstance(payload, dict):
        return None
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    if tool in SHELL_TOOLS:
        return command_names_env_file(tool_input.get("command"))
    for field, names_env in PATH_FIELDS.get(tool, {}).items():
        value = tool_input.get(field)
        if names_env(value):
            return value
    return None


def denial(target: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Blocked by project hook: .env files may not be read or edited "
                f"(matched '{target}'). .env holds live credentials and anything "
                f"read lands in the session transcript. Use .env.example for "
                f"variable names, or ask the user to supply a value. "
                f"See .claude/hooks/env_guard.py."
            ),
        }
    }


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        print(f"[env_guard] unparseable hook payload, allowing: {exc}", file=sys.stderr)
        return
    target = evaluate(payload)
    if target:
        print(json.dumps(denial(target)))


if __name__ == "__main__":
    main()
