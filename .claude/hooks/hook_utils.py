"""
.claude/hooks/hook_utils.py

Helpers shared by the hook scripts in this directory, so each rule has one
definition:

  - project_dir(): the project root every hook anchors on.
  - absolute_path(): one resolution of a path string an agent wrote in a
    tool call (Git Bash, WSL, device-prefix and drive-relative forms).
  - project_path(): how every hook resolves a configured value
    (PRIVATE_VAULT_PATH, VAULT_PATH_TEST). It delegates to
    src/core/vault_path.py, the rule the app's get_private_vault_path()
    uses, so vault_guard.py, secret_dump_guard.py, post_commit_eval.py and
    the app agree on which directory is the live vault.
  - command_name(): one "bare command name" rule (env_guard.py,
    secret_dump_guard.py).
  - read_env_value() / env_file_value(): one .env value reader
    (post_commit_eval.py, vault_guard.py). It never prints a value.
  - shell_step() / close_paren() / extract_substitutions() / shell_spans() /
    drop_inert_heredocs(): the shell scanner env_guard.py and
    secret_dump_guard.py share (quote state with backslash escapes, command
    substitution, quoted spans, heredocs whose receiver only stores them).
  - deny(): the PreToolUse deny decision the harness reads from stdout
    (env_guard.py, vault_guard.py, secret_dump_guard.py).
  - deny_on_error(): the fail-closed deny the vault guards emit when their
    own check raises.

Deliberately pathlib- and typing-free: PreToolUse guards start a fresh
Python process on every tool call, and those imports cost ~11 ms each time.
"""

import json
import os
import re


# The checkout this file lives in (.claude/hooks/hook_utils.py -> root).
# Its src/ holds the shared vault-path resolver.
CHECKOUT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def project_dir() -> str:
    """CLAUDE_PROJECT_DIR, falling back to this checkout (a manual run), so
    a hook behaves the same from any session cwd."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or CHECKOUT_ROOT


def absolute_path(path: str, base: str) -> str:
    """`path` as an absolute, lexically normalised path, for every form an
    agent might write. Relative forms resolve against `base`.

    The path-form patterns are literal strings, compiled through re's cache
    on first use: env_guard and post_commit_eval import this module on
    every call and never resolve a path."""
    # \\?\C:\x and \\.\C:\x, matched after backslashes become slashes.
    s = re.sub(r"^//[?.]/", "", path.replace("\\", "/"))
    s = os.path.expanduser(s)
    # Git Bash /c/x and WSL /mnt/c/x.
    m = re.match(r"^/(?:mnt/)?([a-zA-Z])(?=/|$)", s) if os.name == "nt" else None
    if m:
        s = f"{m.group(1)}:/{s[m.end():]}"
    # ntpath.join also resolves drive-relative C:x against `base` when it is
    # on the same drive (the only drive whose cwd the hook knows).
    return os.path.normpath(os.path.join(base, s))


def command_name(word: str) -> str:
    """A command word reduced to a bare lowercase name, so an absolute-path
    or Windows-suffixed utility (C:\\Windows\\System32\\more.com,
    /usr/bin/cat, bash.exe) matches its plain name."""
    name = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith((".exe", ".com")) else name


_resolve_configured_path = None


def _vault_resolver():
    """resolve_configured_path from this checkout's src/core/vault_path.py,
    loaded by file path under a private module name and cached.

    Not `import src.core.vault_path`: any other top-level `src` package on
    sys.path (PYTHONPATH, a .pth file) would shadow it, the import would
    raise, and a guard that crashes lets the tool call through. Loaded on
    first use so env_guard never pays for it."""
    global _resolve_configured_path
    if _resolve_configured_path is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_ember_hook_vault_path",
            os.path.join(CHECKOUT_ROOT, "src", "core", "vault_path.py"),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _resolve_configured_path = module.resolve_configured_path
    return _resolve_configured_path


def project_path(value: str) -> str | None:
    """A configured path (PRIVATE_VAULT_PATH, VAULT_PATH_TEST) by the app's
    rule, src/core/vault_path.resolve_configured_path, anchored on the
    project root rather than the hook process cwd. None for a blank value."""
    return _vault_resolver()(value, project_dir())


def read_env_value(key: str, env_path) -> str | None:
    """Return `key` from the process env, falling back to the .env file at
    `env_path`. Hooks run as bare subprocesses, so .env has not been loaded
    into the environment."""
    return (os.environ.get(key) or "").strip() or env_file_value(key, env_path)


def env_file_value(key: str, env_path) -> str | None:
    """Return `key` from the .env file at `env_path` only.

    The value comes from python-dotenv (the venv has it), with a minimal
    KEY=VALUE parser as a fallback so a hook never hard-depends on an
    import being present.
    """
    try:
        from dotenv.main import DotEnv

        # override=False is how the app loads this file (src/core/config.py:
        # load_dotenv's default): in a `${VAR}` reference the process
        # environment wins over the file's own keys. dotenv_values() uses
        # override=True, the reverse, and would point the guards at another
        # directory whenever the two define the same name.
        values = DotEnv(env_path, encoding="utf-8", interpolate=True, override=False).dict()
        return (values.get(key) or "").strip() or None
    except Exception:
        pass

    try:
        with open(env_path, encoding="utf-8-sig") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return None
    return _fallback_value(key, lines)


# One `[export] KEY = value` line, and python-dotenv's `${NAME}` /
# `${NAME:-default}` reference.
_ENV_LINE = re.compile(r"(?:export[ \t]+)?([^=#\s]+)[ \t]*=[ \t]*(.*)")
_ENV_REFERENCE = re.compile(r"\$\{([^}:]*)(?::-([^}]*))?\}")


def _fallback_value(key: str, lines: list) -> str | None:
    """`key` from .env `lines`, read as python-dotenv's dotenv_values reads
    them (a BOM, `export`, quoted values with escapes, an unquoted value's
    ` # comment`, `${VAR}` and `${VAR:-default}` resolved from the process
    environment first, then the keys above it, as the app's load_dotenv with
    override=False does). The fallback must agree with dotenv: a different
    PRIVATE_VAULT_PATH points the vault guards at the wrong directory. A
    line this parser cannot read the way dotenv does (an unclosed quote, a
    quoted value followed by text, a reference left unresolved, such as a
    nested default) raises ValueError; the vault guards deny when a check
    raises, so no guess is made."""
    values: dict = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.fullmatch(line)
        if not match:
            continue
        name, text = match.groups()
        value = _fallback_literal(text)
        if "${" in value:
            env = {**values, **os.environ}
            value = _ENV_REFERENCE.sub(lambda m: env[m[1]] if m[1] in env else (m[2] or ""), value)
            if "${" in value:
                raise ValueError("unresolved reference in .env value")
        values[name] = value
    return (values.get(key) or "").strip() or None


def _fallback_literal(text: str) -> str:
    """One value as dotenv reads it, before `${}` references are resolved."""
    if text[:1] in ("'", '"'):
        quote = text[0]
        match = re.match(quote + r"((?:\\.|[^" + quote + r"\\])*)" + quote + r"[ \t]*(?:#.*)?", text)
        if not match or match.end() != len(text):
            raise ValueError("unreadable quoted .env value")
        body = match[1]
        if quote == "'":
            return re.sub(r"\\([\\'])", r"\1", body)
        return re.sub(
            r"\\[\\'\"abfnrtv]", lambda m: m[0].encode("ascii").decode("unicode-escape"), body
        )
    return re.sub(r"\s+#.*", "", text).rstrip()


_ENV_REFERENCE_NAME = re.compile(r"\$\{([^}:]*)")


def env_references(env_path) -> list:
    """The process-environment values of every `${NAME}` the file at
    `env_path` references, as sorted [name, value] pairs. Part of the
    PRIVATE_VAULT_PATH cache key: a reference resolves from the process
    environment first, so the cached result is stale when one of them
    changes."""
    try:
        with open(env_path, encoding="utf-8-sig") as handle:
            names = set(_ENV_REFERENCE_NAME.findall(handle.read()))
    except OSError:
        return []
    return [[name, os.environ.get(name)] for name in sorted(names)]


# ---------------------------------------------------------------------------
# Shell scanning shared by the guards: quote state with backslash escapes,
# command substitution, and heredocs. One scanner, so a quote or an escape is
# read the same way by every rule.
# ---------------------------------------------------------------------------

# Characters a backslash escapes, by quote state. Any other backslash is a
# path separator here (C:\x\y), not an escape.
_ESCAPABLE = {"": "'\"`$\\", '"': '"`$\\', "'": ""}


def shell_step(text: str, i: int, quote: str) -> tuple[int, str]:
    """(length of the piece at `i`, quote state after it). A piece is one
    character, or a two-character backslash escape (`\\'`, `\\"`, `\\$`, a
    backtick, `\\\\`) outside single quotes. `quote` is "", "'" or '"'."""
    ch = text[i]
    if ch == "\\" and i + 1 < len(text) and text[i + 1] in _ESCAPABLE[quote]:
        return 2, quote
    if quote:
        return 1, "" if ch == quote else quote
    return 1, ch if ch in "'\"" else ""


def close_paren(text: str, i: int) -> int:
    """Index of the `)` closing a `(` whose body starts at `i`, quote-aware;
    the end of `text` when unclosed."""
    depth, quote, n = 1, "", len(text)
    while i < n:
        size, after = shell_step(text, i, quote)
        if not quote and not after:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    return i
        quote, i = after, i + size
    return n


def extract_substitutions(text: str) -> tuple[str, list[str]]:
    """`text` with each command substitution replaced by an index marker
    (\\x01N\\x01), and the substitution bodies. Active outside quotes and
    inside double quotes: `$(...)` and backticks; outside quotes only:
    `<(...)` and `>(...)`. Literal inside single quotes and when escaped."""
    if "$(" not in text and "`" not in text and "<(" not in text and ">(" not in text:
        return text, []
    out: list[str] = []
    bodies: list[str] = []
    quote = ""
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        end = -1
        if quote != "'":
            if ch == "`":
                end = text.find("`", i + 1)
                end = n if end < 0 else end
                body = text[i + 1 : end]
            elif ch in "$<>" and text.startswith("(", i + 1) and (ch == "$" or not quote):
                end = close_paren(text, i + 2)
                body = text[i + 2 : end]
        if end >= 0:
            out.append(f"\x01{len(bodies)}\x01")
            bodies.append(body)
            i = end + 1
            continue
        size, quote = shell_step(text, i, quote)
        out.append(text[i : i + size])
        i += size
    return "".join(out), bodies


def shell_spans(text: str) -> list[str]:
    """`text` cut into closed quoted spans (quotes included), a lone
    unmatched quote, and the unquoted runs between them."""
    spans: list[str] = []
    start, quote, i, n = 0, "", 0, len(text)
    while i < n:
        size, after = shell_step(text, i, quote)
        if not quote and after:  # opens a quote
            if i > start:
                spans.append(text[start:i])
            start = i
        elif quote and not after:  # closes it
            spans.append(text[start : i + size])
            start = i + size
        quote, i = after, i + size
    if quote:  # unterminated: the quote stands alone, the rest is scanned afresh
        spans.append(text[start])
        spans.extend(shell_spans(text[start + 1 :]))
    elif start < n:
        spans.append(text[start:])
    return spans


_HEREDOC_START = re.compile(
    r"(?<!<)<<-?[ \t]*(?P<q>['\"]?)(?P<tag>[A-Za-z_]\w*)(?P=q)(?P<rest>[^\n]*)\n"
)
_PIPED = re.compile(r"(?<!\|)\|(?!\|)")
# The value of a message flag of a git or gh command is prose.
_PROSE_FLAG = re.compile(r"(?:^|\s)(?:-a?m|--message|--body|--title|--notes)(?:\s+|=)\"?$")
_GIT_OR_GH = re.compile(r"^\s*(?:git|gh)\s")
_STDIN_FILE_FLAGS = {"git": ("-F", "--file"), "gh": ("-F", "--body-file")}
_ASSIGNMENT_WORD = re.compile(r"[A-Za-z_]\w*=")


def _last_separator(text: str) -> int:
    """Index of the last unquoted `; & | ( newline` or backtick in `text`, or
    -1. A `$(` inside double quotes opens a fresh unquoted context, so the
    commit idiom `-m "$(cat <<'EOF'` finds the `(`."""
    sep, quote, i = -1, "", 0
    resume: list[tuple[int, str]] = []  # (paren depth, quote) to return to at the matching `)`
    depth = 0
    while i < len(text):
        ch = text[i]
        if quote == '"' and ch == "$" and text[i + 1 : i + 2] == "(":
            resume.append((depth, quote))
            depth, quote, sep, i = 0, "", i + 1, i + 2
            continue
        size, after = shell_step(text, i, quote)
        if not quote and not after:
            if ch == ")":
                if depth:
                    depth -= 1
                elif resume:
                    depth, after = resume.pop()
            elif ch in (";", "&", "|", "`", "\n"):
                sep = i
            elif ch == "(":
                depth, sep = depth + 1, i
        quote, i = after, i + size
    return sep


def _heredoc_is_inert(before: str, rest: str) -> bool:
    """True when the receiver of a heredoc only stores or displays its body:
    `cat` whose output is not used as words, `tee`, `git ... -F -` and
    `gh ... --body-file -`. `before` is the text preceding `<<`, `rest` the
    remainder of its line. Every other receiver (a shell, an interpreter,
    sqlite3, xargs, versioned python3.12, ...) may run the body, and so does
    a pipe on the line."""
    if _PIPED.search(rest):
        return False
    sep = _last_separator(before)
    in_substitution = sep >= 0 and (before[sep] == "`" or (before[sep] == "(" and before[sep - 1 : sep] == "$"))
    if in_substitution:
        # The output of $(...) becomes words of the outer command: prose is
        # the one use that does not run or read anything.
        outer = before[: sep - 1] if before[sep] == "(" else before[:sep]
        segment = outer[_last_separator(outer) + 1 :]
        if not (_GIT_OR_GH.match(segment) and _PROSE_FLAG.search(segment)):
            return False
    try:
        import shlex

        words = [w for w in shlex.split(before[sep + 1 :]) if not _ASSIGNMENT_WORD.match(w)]
    except ValueError:
        return False
    name = command_name(words[0]) if words else ""
    if name in ("cat", "tee"):
        return True
    flags = _STDIN_FILE_FLAGS.get(name, ())
    for k, word in enumerate(words):
        flag, eq, value = word.partition("=")
        if word == "-F-" and "-F" in flags:
            return True
        if flag in flags and (value == "-" if eq else words[k + 1 : k + 2] == ["-"]):
            return True
    return False


def drop_inert_heredocs(text: str, literal_only: bool = True) -> str:
    """`text` without the bodies of heredocs whose receiver only stores or
    displays them (_heredoc_is_inert). A body behind a quoted delimiter
    (<<'EOF') is literal. Behind a bare one (<<EOF) the shell still expands
    `$(...)` in it, so with `literal_only` False the body is replaced by those
    substitutions, and with True it stays as it is."""
    if "<<" not in text:
        return text
    text = text.replace("\r\n", "\n")
    out: list[str] = []
    pos = 0
    while True:
        match = _HEREDOC_START.search(text, pos)
        if not match:
            break
        end = re.compile(rf"^[ \t]*{match['tag']}[ \t]*$", re.MULTILINE).search(text, match.end())
        if not end or not _heredoc_is_inert(text[: match.start()], match["rest"]):
            out.append(text[pos : match.end()])
            pos = match.end()
            continue
        literal = bool(match["q"])
        body = text[match.end() : end.start()]
        if not literal and literal_only:
            out.append(text[pos : end.end()])
        else:
            out.append(text[pos : match.start()] + "<< HERE" + match["rest"])
            if not literal:
                out.append("".join(f"\n$({b})" for b in extract_substitutions(body)[1]))
        pos = end.end()
    out.append(text[pos:])
    return "".join(out)


def deny_on_error(guard: str, exc: BaseException) -> None:
    """Fail closed: deny when a guard cannot finish its check. A crashed
    hook exits non-zero, which the harness treats as a non-blocking error
    and lets the tool call run. Only the exception type is printed; its
    message can carry the configured vault path."""
    deny(
        f"{guard} could not complete its vault check "
        f"({type(exc).__name__}); denying rather than allowing an "
        f"unchecked call. See .claude/hooks/{guard}.py."
    )


def deny(reason: str) -> None:
    """Print a PreToolUse deny decision with `reason`."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
