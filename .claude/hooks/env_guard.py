"""
.claude/hooks/env_guard.py

PreToolUse hook for Read|Grep|Glob|Bash|PowerShell -- denies agent tool
calls that name a .env file (.env, .env.local, .env.production, ...).
.env.example is allowed everywhere.

Why: .env holds live credentials, and anything a tool reads lands in the
session transcript. A transcript is not a secret store. This hook refuses
the call before it runs. Edit|Write to .env files is denied by
vault_guard.py, which imports names_env_file() from here so there is one
definition of "a .env file."

Port of ember-2-ui .claude/hooks/block-env-reads.cjs (PR #64):
  - Every path-like string is normalised backslash -> slash and matched on
    its basename, so C:\\dev\\x\\.env, C:/dev/x/.env, /c/dev/x/.env and
    .env are the same thing.
  - Read/Grep/Glob: only the input fields that can name a file are checked
    (PATH_FIELDS). Grep's `pattern` is a regex over contents, not a path.
  - Bash/PowerShell: every token of the command is checked, whatever the
    utility (cat, gc, sed, grep, findstr, python -c, an absolute-path
    more.com, ...). Tokens split on whitespace, quotes, shell separators,
    redirections and `=`, never on backslash, so Windows paths survive.
  - Globs: only a pattern whose literal prefix (before the first wildcard)
    could still reach a .env file is denied. `.e*`, `.env*` deny; `.git*`
    and `.env.example*` do not.
  - Grep's `glob` additionally gets ripgrep semantics (`*` matches a
    leading dot and overrides .gitignore), so `*` and `*.local` deny there.
    See grep_glob_reaches_env().
  - Prose skip: the quoted value of -m/--message/--body/--title/--notes on
    a git or gh segment is dropped before scanning, so a commit message
    that mentions .env does not trip the hook. Nothing else is skipped.

Windows extras beyond the reference: trailing dots/spaces on a basename
are stripped (Win32 resolves `.env.` to `.env`), and a basename is also
checked per colon-separated part (`.env::$DATA` stream syntax, `C:.env`
drive-relative syntax).

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

# Which input fields of each non-shell tool can name a file.
PATH_FIELDS = {
    "Read": ("file_path",),
    "Grep": ("path", "glob"),  # Grep's `pattern` is a content regex, not a path
    "Glob": ("path", "pattern"),
}
SHELL_TOOLS = frozenset({"Bash", "PowerShell"})


def _basename(path: str) -> str:
    trimmed = path.replace("\\", "/").rstrip("/")
    return trimmed.rsplit("/", 1)[-1]


def _base_names_env(base: str) -> bool:
    """True when a single basename (no separators) names a .env file."""
    # Win32 path normalisation drops trailing dots and spaces: `.env.` is `.env`.
    base = base.rstrip(". ")
    lower = base.lower()
    # The example file itself, or a glob that can only reach it.
    if not base or lower == _EXAMPLE or (_WILDCARD.search(lower) and lower.startswith(_EXAMPLE)):
        return False
    if _ENV_NAME.match(base):
        return True

    # Globs. Only a pattern that starts with a literal dot can match a
    # dotfile (`*` alone does not match `.env`). Compare the literal prefix
    # before the first wildcard: `.e*`, `.en?`, `.env*` and `.env.s*` can
    # all reach a .env file, `.git*` cannot.
    wild = _WILDCARD.search(base)
    if wild and base.startswith("."):
        literal = lower[: wild.start()]
        return literal.startswith(".env") or ".env".startswith(literal)

    # Brace sets: `.env{,.local}` / `{.env,.env.local}`.
    alternatives = _expand_braces(base)
    if len(alternatives) > 1:
        return any(_base_names_env(alt) for alt in alternatives)
    return False


def names_env_file(candidate) -> bool:
    """True when `candidate` (a path, filename, or glob) names a protected
    .env file. .env.example and globs that can only reach it are allowed."""
    if not isinstance(candidate, str) or not candidate:
        return False
    base = _basename(candidate)
    # `.env::$DATA` (NTFS stream) and `C:.env` (drive-relative) both name
    # .env on Windows; check the whole basename and each colon-separated part.
    return any(_base_names_env(part) for part in [base, *base.split(":")])


# Grep's `glob` is a ripgrep glob, and ripgrep differs from the dotfile rule
# above in two ways: `*` DOES match a leading dot, and a matching -g glob
# overrides .gitignore. So `glob: "*"` or `"*.local"` makes Grep print .env
# contents (verified live). A Grep glob is therefore also tested against
# representative .env names. A negated glob (`!*.py`) adds no whitelist, so
# .gitignore still applies and it is left alone.
_GREP_GLOB_PROBES = (".env", ".env.local", ".env.development", ".env.production", ".env.test")


def _expand_braces(pattern: str) -> list[str]:
    brace = _BRACE.match(pattern)
    if not brace:
        return [pattern]
    head, alts, tail = brace.groups()
    return [p for alt in alts.split(",") for p in _expand_braces(f"{head}{alt}{tail}")]


def grep_glob_reaches_env(glob) -> bool:
    """True when a Grep (ripgrep) glob could select a .env file."""
    if names_env_file(glob):
        return True
    if not isinstance(glob, str) or not glob or glob.startswith("!"):
        return False
    base = _basename(glob).lower()
    return any(
        fnmatch.fnmatchcase(probe, alt)
        for alt in _expand_braces(base)
        for probe in _GREP_GLOB_PROBES
    )


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
    if not isinstance(command, str) or not command:
        return None
    for token in _TOKEN_SPLIT.split(_without_prose(command)):
        if token and names_env_file(token):
            return token
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
        return command_names_env_file(tool_input.get("command"))
    if tool == "Grep" and grep_glob_reaches_env(tool_input.get("glob")):
        return tool_input.get("glob")
    for field in PATH_FIELDS.get(tool, ()):
        value = tool_input.get(field)
        if names_env_file(value):
            return value
    return None


def denial(target: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Blocked by project hook: reading .env files is not allowed "
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
