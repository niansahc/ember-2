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
    (post_commit_eval.py, vault_guard.py). It never prints a value. Its
    strict mode (the vault guards) raises on a file that exists but cannot
    be read and on a value left unresolved (require_resolved()).
    env_references() is the vault cache's digest of referenced variables,
    None when the file cannot be read.
  - shell_step() / close_paren() / extract_substitutions() / shell_spans() /
    drop_inert_heredocs(): the text scanner env_guard.py uses (quote state
    with backslash escapes, command substitution, quoted spans, heredocs
    whose receiver only stores them).
  - parse_shell() / pipelines() / expand_braces() / output_words() /
    literal_heredocs(): the shell parser secret_dump_guard.py uses (words
    with their quoting, substitutions, heredocs, pipe stages, Bash brace
    expansion, heredoc bodies no command runs). A Python port of the
    ember-2-installer parser (.claude/hooks/hook-utils.cjs).
  - run_hook(): the one entry of the three PreToolUse guards (env_guard.py,
    vault_guard.py, secret_dump_guard.py): stdin, the input caps
    (MAX_COMMAND_CHARS, MAX_PATH_CHARS), InputLimit denies, fail closed
    (deny_on_error), and the deny() decision on stdout.
  - SHELL_TOOLS, PATH_FIELDS, and MAX_LIVE_BRACES / MAX_BRACE_WORDS for
    env_guard.py's brace expansion.

Deliberately pathlib- and typing-free: PreToolUse guards start a fresh
Python process on every tool call, and those imports cost ~11 ms each time.
"""

import json
import os
import re
import sys


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


def env_file_value(key: str, env_path, strict: bool = False) -> str | None:
    """Return `key` from the .env file at `env_path` only.

    With `strict` (the vault guards), a file that exists but cannot be read
    (no permission, a lock, a directory) raises OSError, and a value still
    holding `${` after interpolation raises ValueError, instead of reading
    as "no key" or as a path. The guards deny on either, as the
    ember-2-installer vault guard does (ember-2-installer/.claude/hooks/
    vault-guard.cjs, envFileValue). A missing file is None either way.
    The file is opened once and its text handed to the parser: python-dotenv
    reads a directory as an empty file.

    The value comes from python-dotenv (the venv has it), with a minimal
    KEY=VALUE parser as a fallback so a hook never hard-depends on an
    import being present. The fallback raises ValueError on an unresolved
    reference itself, in either mode.
    """
    try:
        with open(env_path, encoding="utf-8-sig") as handle:
            text = handle.read()
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError:
        if strict:
            raise
        return None
    try:
        value = _dotenv_value(key, text)
    except Exception:
        return _fallback_value(key, text.splitlines())
    if strict:
        require_resolved(value)
    return value


def require_resolved(value) -> None:
    """Raise ValueError when a configured value still holds `${` (a reference
    python-dotenv could not resolve: unterminated, or a nested default). The
    vault guards apply it to a fresh read and to a cached value alike."""
    if isinstance(value, str) and "${" in value:
        raise ValueError("unresolved reference in .env value")


def _dotenv_value(key: str, text: str) -> str | None:
    """`key` as python-dotenv reads it from the file's text. Raises when
    python-dotenv is not importable or fails."""
    import io

    from dotenv.main import DotEnv

    # override=False is how the app loads this file (src/core/config.py:
    # load_dotenv's default): in a `${VAR}` reference the process
    # environment wins over the file's own keys. dotenv_values() uses
    # override=True, the reverse, and would point the guards at another
    # directory whenever the two define the same name.
    values = DotEnv(None, stream=io.StringIO(text), interpolate=True, override=False).dict()
    return (values.get(key) or "").strip() or None


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
    nested default) is unreadable. Only that line's own key suffers: the
    lookup raises ValueError when `key` itself, or a variable its value
    references, ends up unreadable, and the vault guards deny when a check
    raises, so no guess is made. An unreadable line of any other key is
    skipped, as dotenv skips it."""
    values: dict = {}
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _ENV_LINE.fullmatch(line)
        if not match:
            continue
        name, text = match.groups()
        try:
            value = _fallback_literal(text)
            if "${" in value:
                env = {**values, **os.environ}
                value = _ENV_REFERENCE.sub(_reference(env), value)
                if "${" in value:
                    raise ValueError("unresolved reference in .env value")
        except ValueError:
            value = _UNREADABLE
        values[name] = value
    result = values.get(key)
    if result is _UNREADABLE:
        raise ValueError("unreadable .env value")
    return (result or "").strip() or None


# The value of a key whose line could not be read (see _fallback_value).
_UNREADABLE = object()


def _reference(env: dict):
    """The substitution for one `${NAME}` / `${NAME:-default}` match."""
    def resolve(match) -> str:
        name, default = match.groups()
        if name not in env:
            return default or ""
        if env[name] is _UNREADABLE:
            raise ValueError("reference to an unreadable .env value")
        return env[name]
    return resolve


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


def env_references(env_path) -> str | None:
    """A digest of the process-environment values of every `${NAME}` the file
    at `env_path` references ("" when it has none, or there is no file).
    Part of the PRIVATE_VAULT_PATH cache key: a reference resolves from the
    process environment first, so the cached result is stale when one of
    them changes. A digest, so a referenced value is never copied into the
    cache file. None when the file exists but cannot be read: the
    references are unknown, so no key can be built and the cache is
    skipped."""
    try:
        with open(env_path, encoding="utf-8-sig") as handle:
            names = set(_ENV_REFERENCE_NAME.findall(handle.read()))
    except (FileNotFoundError, NotADirectoryError):
        return ""
    except OSError:
        return None
    if not names:
        return ""
    pairs = json.dumps([[name, os.environ.get(name)] for name in sorted(names)])
    # Imported here: only vault_guard's cache key needs it, and loading
    # OpenSSL costs ~6 ms on every guard call.
    import hashlib

    return hashlib.sha256(pairs.encode("utf-8")).hexdigest()


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
# The one list of message flags whose value is prose on a git or gh command
# (env_guard.py's prose rule and the parser's prose_values use it), as words
# and as a regex alternation, and the git/gh command test.
PROSE_FLAGS = ("-m", "-am", "--message", "--body", "--title", "--notes")
PROSE_FLAG_NAMES = "(?:" + "|".join(PROSE_FLAGS) + ")"
GIT_OR_GH = re.compile(r"^\s*(?:git|gh)\s")
_PROSE_FLAG = re.compile(r"(?:^|\s)" + PROSE_FLAG_NAMES + r"(?:\s+|=)\"?$")
# The flags of git and gh that read a message from a file (`-` is stdin).
_STDIN_FILE_FLAGS = {"git": ("-F", "--file"), "gh": ("-F", "--body-file")}
_ASSIGNMENT_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
# Characters of command text before a heredoc's `<<` that _heredoc_is_inert
# will read; past this the heredoc counts as not inert.
_MAX_HEREDOC_COMMAND = 1024


def _separator_index(text: str) -> tuple[list[int], list[str]]:
    """(seps, quotes) for every p in 0..len(text): seps[p] is the index of
    the last unquoted `; & | ( newline` or backtick in text[:p], or -1, and
    quotes[p] the quote state ("", "'" or '"') at the end of text[:p]. A
    `$(` inside double quotes opens a fresh unquoted context, so the commit
    idiom `-m "$(cat <<'EOF'` finds the `(`.

    One pass for all prefixes: drop_inert_heredocs needs the separator before
    every heredoc, and one scan per heredoc was quadratic (2730 heredocs in
    16 K characters took 6 s). A step is one character or a two-character
    escape (shell_step), and only the `$(` check looks past a cut, so each
    prefix gets exactly what a scan of that prefix alone would give
    (tests/test_hook_input_caps.py compares the two)."""
    n = len(text)
    seps = [-1] * (n + 1)
    quotes = [""] * (n + 1)
    sep, quote, i, depth = -1, "", 0, 0
    resume: list[tuple[int, str]] = []  # (paren depth, quote) to return to at the matching `)`
    while i < n:
        ch = text[i]
        if quote == '"' and ch == "$" and text[i + 1 : i + 2] == "(":
            # A prefix cut between `$` and `(` sees a plain `$`.
            seps[i + 1], quotes[i + 1] = sep, quote
            resume.append((depth, quote))
            depth, quote, sep = 0, "", i + 1
            seps[i + 2], quotes[i + 2] = sep, quote
            i += 2
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
        for k in range(i + 1, i + size + 1):
            seps[k], quotes[k] = sep, after
        quote, i = after, i + size
    return seps, quotes


def _heredoc_is_inert(text: str, start: int, rest: str, index) -> bool:
    """True when the receiver of the heredoc whose `<<` is at text[start]
    only stores or displays its body: `cat` whose output is not used as
    words, `tee`, `git ... -F -` and `gh ... --body-file -`. `rest` is the
    remainder of its line, `index` is _separator_index(text). Every other
    receiver (a shell, an interpreter, sqlite3, xargs, versioned python3.12,
    ...) may run the body, and so does a pipe on the line."""
    seps, quotes = index
    # A `<<` inside quotes is text, not a heredoc, and its segment holds an
    # unclosed quote that shlex rejects below; answering here keeps the
    # cost of each heredoc off the length of the text before it.
    if quotes[start] or _PIPED.search(rest):
        return False
    sep = seps[start]
    # The command before `<<` is short in real use. A longer one keeps its
    # body scanned (the conservative answer), which bounds the shlex work per
    # heredoc: several heredocs sharing one quote-balanced segment re-split
    # all of it each time (`<<A'\nA\n'` x 1820 took 5 s).
    if start - sep - 1 > _MAX_HEREDOC_COMMAND:
        return False
    in_substitution = sep >= 0 and (text[sep] == "`" or (text[sep] == "(" and text[sep - 1 : sep] == "$"))
    if in_substitution:
        # The output of $(...) becomes words of the outer command: prose is
        # the one use that does not run or read anything.
        outer_end = sep - 1 if text[sep] == "(" else sep
        segment = text[seps[outer_end] + 1 : outer_end]
        if not (GIT_OR_GH.match(segment) and _PROSE_FLAG.search(segment)):
            return False
    try:
        import shlex

        words = [w for w in shlex.split(text[sep + 1 : start]) if not _ASSIGNMENT_WORD.match(w)]
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
    index = None  # _separator_index(text), built on the first heredoc
    while True:
        match = _HEREDOC_START.search(text, pos)
        if not match:
            break
        end = re.compile(rf"^[ \t]*{match['tag']}[ \t]*$", re.MULTILINE).search(text, match.end())
        if end and index is None:
            index = _separator_index(text)
        if not end or not _heredoc_is_inert(text, match.start(), match["rest"], index):
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


# ---------------------------------------------------------------------------
# Shell parser: a port of ember-2-installer .claude/hooks/hook-utils.cjs
# (parseShell, pipelines, expandBraces, literalHeredocs), Bash flavor. One
# quote- and escape-aware pass gives words, operators, substitutions and
# heredocs, so a quote, an escape or a brace is read the way the shell reads
# it before any rule looks at a word.
# ---------------------------------------------------------------------------

# Nesting deeper than this (substitutions inside substitutions) is not
# followed: parse_shell raises NestingLimit and the caller denies.
MAX_DEPTH = 8
# Bash brace expansion caps, besides MAX_LIVE_BRACES and MAX_BRACE_WORDS
# (words from one word): words added in one pipe stage, and expansion steps
# in one word. Past a cap nothing is expanded (BraceOverflow).
MAX_BRACE_STAGE = 1024
MAX_BRACE_STEPS = 32


class NestingLimit(Exception):
    """A substitution nested past MAX_DEPTH."""


class BraceOverflow(Exception):
    """A brace expansion past the caps."""


# Characters that end a word and stand as operators (with `(` and `)`). In
# Bash `{` and `}` are operators only as a word of their own.
_OPERATORS = ";<>|&{}"


class Word:
    """One parsed word. `text`: its value, a literal backslash read as `/`
    (so a Windows path stays a path); `bare`: the part read outside quotes;
    `live`: one char per char of `text`, "1" where Bash may read it as
    expansion syntax (unquoted, unescaped, outside ${...}), else "0";
    `parts`: [(quote, text)] runs by the quote they were read in (None, '
    or "); `subs`: the parsed substitutions inside it; `src`: `text` with
    each substitution put back as $(body), the command line a nested shell
    receives; `start`/`end`: offsets in the command; `open`: ends inside a
    quote left open; `escaped`: a backslash escape was read in it."""

    __slots__ = ("text", "bare", "live", "parts", "subs", "src", "start", "end", "open", "escaped")


class Heredoc:
    """A heredoc operator and its body. `quoted`: the delimiter word has any
    quoting (<<'EOF', <<\\EOF); `body`: the raw text between the operator
    line and the terminator line; `subs`: the substitutions Bash runs in an
    unquoted body; `terminated`: a terminator line was found."""

    __slots__ = ("quoted", "strip", "delimiter", "body", "subs", "start", "end", "terminated")


class Script:
    """A parsed command: `tokens` are operator strings, Words and Heredocs."""

    __slots__ = ("tokens", "bash")

    def __init__(self, tokens, bash):
        self.tokens = tokens
        self.bash = bash


class _WordBuilder:
    """A word being read: lists joined once by finish(), so a long word costs
    linear time."""

    __slots__ = ("text", "bare", "live", "parts", "subs", "src", "start", "escaped")

    def __init__(self, start):
        self.text, self.bare, self.live, self.parts, self.subs, self.src = [], [], [], [], [], []
        self.start = start
        self.escaped = False

    def add(self, piece: str, quote, literal: bool) -> None:
        if self.parts and self.parts[-1][0] == quote:
            self.parts[-1][1].append(piece)
        else:
            self.parts.append((quote, [piece]))
        self.text.append(piece)
        self.src.append(piece)
        self.live.append(("1" if quote is None and not literal else "0") * len(piece))
        if quote is None:
            self.bare.append(piece)

    def finish(self, end: int, open_quote: bool) -> Word:
        word = Word()
        word.text = "".join(self.text)
        word.bare = "".join(self.bare)
        word.live = "".join(self.live)
        word.parts = [(quote, "".join(pieces)) for quote, pieces in self.parts]
        word.subs = self.subs
        word.src = "".join(self.src)
        word.start, word.end, word.open = self.start, end, open_quote
        word.escaped = self.escaped
        return word


class _ShellParser:
    """parse_shell's state: the command and the read position."""

    def __init__(self, command: str, flavor: str, backslash_literal: bool):
        self.s = command
        self.i = 0
        self.bash = flavor == "bash"
        # The second reading of a Bash command: every backslash is a path
        # separator, as PowerShell and cmd read one.
        self.escapes = self.bash and not backslash_literal

    def heredoc_operator(self):
        """The heredoc whose `<<` ends at s[i], or None when no delimiter word
        follows."""
        s, j = self.s, self.i
        strip = s[j : j + 1] == "-"
        if strip:
            j += 1
        while j < len(s) and s[j] in " \t":
            j += 1
        delimiter: list[str] = []
        quoted = False
        while j < len(s) and not (s[j].isspace() or s[j] in ";&|<>()"):
            c = s[j]
            if c in "'\"":
                end = s.find(c, j + 1)
                if end < 0:
                    return None
                delimiter.append(s[j + 1 : end])
                quoted = True
                j = end + 1
            elif c == "\\":
                delimiter.append(s[j + 1 : j + 2])
                quoted = True
                j += 2
            else:
                delimiter.append(c)
                j += 1
        if not delimiter or not "".join(delimiter):
            return None
        self.i = min(j, len(s))
        heredoc = Heredoc()
        heredoc.quoted, heredoc.strip, heredoc.delimiter = quoted, strip, "".join(delimiter)
        heredoc.body, heredoc.subs = "", []
        heredoc.start = heredoc.end = self.i
        heredoc.terminated = False
        return heredoc

    def read_bodies(self, heredocs: list, level: int) -> None:
        """The bodies of `heredocs`, one after another from s[i] (the line
        after the operator)."""
        s, n = self.s, len(self.s)
        for heredoc in heredocs:
            heredoc.start = self.i
            body_end = following = n
            line = self.i
            while line < n:
                nl = s.find("\n", line)
                text = s[line : n if nl < 0 else nl]
                if text.endswith("\r"):
                    text = text[:-1]
                if heredoc.strip:
                    text = text.lstrip("\t")
                if text == heredoc.delimiter:
                    heredoc.terminated = True
                    body_end = line
                    following = n if nl < 0 else nl + 1
                    break
                if nl < 0:
                    break
                line = nl + 1
            heredoc.body = s[heredoc.start : body_end]
            heredoc.end = following
            # Bash runs $(...) and `...` in an unquoted body; quotes there are text.
            j = heredoc.start
            while not heredoc.quoted and j < body_end:
                c = s[j]
                if c == "\\":
                    j += 2
                    continue
                if (c == "$" and s[j + 1 : j + 2] == "(") or c == "`":
                    self.i = j + 1 if c == "`" else j + 2
                    arith = s[self.i : self.i + 1] == "("
                    heredoc.subs.append(self.script(level + 1, "`" if c == "`" else ")", arith))
                    j = self.i
                    continue
                j += 1
            self.i = following

    def script(self, level: int, close, arith: bool) -> Script:
        """One script level: the whole command, or a substitution body ending
        at `close` (")" or "`"). `arith`: a $(( )) body, where << is a shift."""
        if level > MAX_DEPTH:
            raise NestingLimit
        s, n, bash, escapes = self.s, len(self.s), self.bash, self.escapes
        tokens: list = []
        pending: list = []
        word = None
        quote = None
        parens = 0

        def cur() -> _WordBuilder:
            nonlocal word
            if word is None:
                word = _WordBuilder(self.i)
            return word

        def append(ch: str, literal: bool = False) -> None:
            cur().add("/" if ch == "\\" else ch, quote, literal)

        def flush() -> None:
            nonlocal word
            if word is not None:
                tokens.append(word.finish(self.i, quote is not None))
            word = None

        def sub(skip: int, end: str) -> None:
            is_arith = end == ")" and s[self.i + skip : self.i + skip + 1] == "("
            builder = cur()
            self.i += skip
            body_start = self.i
            builder.subs.append(self.script(level + 1, end, is_arith))
            builder.src.append(f"$({s[body_start : max(body_start, self.i - 1)]})")

        def in_quote() -> None:
            nonlocal quote
            if s[self.i] == quote:
                self.i += 1
                quote = None
            else:
                append(s[self.i])
                self.i += 1

        while self.i < n:
            i = self.i
            c = s[i]
            nxt = s[i + 1] if i + 1 < n else None
            if quote == "'":
                in_quote()
            elif escapes and c == "\\":
                if nxt == "\n":
                    self.i += 2  # line continuation
                elif nxt is None or (quote == '"' and nxt not in '$`"\\'):
                    append(c)
                    self.i += 1
                else:
                    append(nxt, True)
                    word.escaped = True
                    self.i += 2
            elif close == "`" and c == "`":
                flush()
                self.i += 1
                break
            elif c == "$" and nxt == "(":
                sub(2, ")")
            elif bash and c == "`":
                sub(1, "`")
            elif quote == '"':
                in_quote()
            elif c in "\"'":
                quote = c
                cur()
                self.i += 1
            elif close == ")" and c == ")" and parens == 0:
                flush()
                self.i += 1
                break
            elif c == "\n":
                flush()
                tokens.append(";")
                self.i += 1
                if pending:
                    self.read_bodies(pending, level)
                pending = []
            elif c in " \t\r":
                flush()
                self.i += 1
            elif c == "#" and word is None:
                nl = s.find("\n", i)
                self.i = n if nl < 0 else nl
            elif c == "$" and nxt == "{":
                end = s.find("}", i)
                stop = n if end < 0 else end + 1
                cur().add(s[i:stop].replace("\\", "/"), quote, True)
                self.i = stop
            elif bash and c in "<>" and nxt == "(":
                # Process substitution: a word of its own (a /dev/fd name).
                flush()
                sub(2, ")")
                flush()
            elif c in "()":
                flush()
                tokens.append(c)
                parens += 1 if c == "(" else -1
                self.i += 1
            elif bash and c in "{}" and not (word is None and (nxt is None or nxt.isspace() or nxt in ";&|()")):
                # A brace is group syntax only as a word of its own ({ cmd; });
                # anywhere else it is word text, read by expand_braces.
                append(c)
                self.i += 1
            elif c in _OPERATORS:
                flush()
                j = i
                while j < n and s[j] in _OPERATORS:
                    j += 1
                op = s[i:j]
                self.i = j
                heredoc = self.heredoc_operator() if bash and not arith and op == "<<" else None
                if heredoc:
                    pending.append(heredoc)
                    tokens.append(heredoc)
                else:
                    tokens.append(op)
            else:
                append(c)
                self.i += 1
        flush()
        return Script(tokens, bash)


def parse_shell(command: str, flavor: str = "bash", depth: int = 0, backslash_literal: bool = False) -> Script:
    """`command` parsed. Bash: '...' is literal; inside "..." a backslash
    escapes only $ ` " \\ and a newline, and $(...) and `...` still run;
    outside quotes \\x is a literal x and a backslash-newline joins lines. A
    quote left open runs to the end of the command and its text is still
    read. A newline is the `;` operator, and `#` at a word start begins a
    comment. With `backslash_literal` every backslash is a path separator
    (`/`) instead, the reading PowerShell and cmd would give. Raises
    NestingLimit past MAX_DEPTH."""
    return _ShellParser(command, flavor, backslash_literal).script(depth, None, False)


class Stage:
    """One pipe stage: its words, redirection targets and heredocs."""

    __slots__ = ("words", "redirs", "heredocs")

    def __init__(self):
        self.words, self.redirs, self.heredocs = [], [], []


def pipelines(script: Script) -> list:
    """The pipelines of a parsed script, each a list of Stages. Pipelines end
    at ; && || & newline and at ( ) { } (a group runs its own commands).
    Empty stages and pipelines are left out."""
    out = [[Stage()]]
    pipeline = out[0]
    stage = pipeline[0]
    redirect = False

    def begin():
        nonlocal pipeline, stage
        pipeline = [Stage()]
        stage = pipeline[0]
        out.append(pipeline)

    for token in script.tokens:
        if isinstance(token, Heredoc):
            stage.heredocs.append(token)
        elif isinstance(token, Word):
            (stage.redirs if redirect else stage.words).append(token)
            redirect = False
        elif token in ("|", "|&"):
            stage = Stage()
            pipeline.append(stage)
        elif any(c in token for c in ";(){}") or token in ("&&", "||", "&"):
            begin()
            redirect = False
        elif "<" in token or ">" in token:
            redirect = True
    return [p for p in ([st for st in p if st.words or st.redirs or st.heredocs] for p in out) if p]


# ---------------------------------------------------------------------------
# Bash brace expansion
# ---------------------------------------------------------------------------

_RANGE = re.compile(r"([+-]?\d+|[A-Za-z])\.\.([+-]?\d+|[A-Za-z])(?:\.\.([+-]?\d+))?")
_NUMBER = re.compile(r"[+-]?\d")
_PADDED = re.compile(r"[+-]?0\d")


def _is_range(body: str) -> bool:
    """A {x..y[..step]} body: two integers or two letters."""
    m = _RANGE.fullmatch(body)
    return bool(m) and bool(_NUMBER.match(m[1])) == bool(_NUMBER.match(m[2]))


# The characters brace expansion reads; every other character is skipped.
_BRACE_CHARS = re.compile(r"[{},]")


def _closing_brace(text: str, live: str, p: int) -> int:
    """The index of the live } closing the live { at `p`, or -1. As in Bash,
    a depth-0 } closes only after a depth-0 comma or when the body so far is
    a range; any other depth-0 } is text ({a},b} is the words a} and b)."""
    depth = commas = 0
    for match in _BRACE_CHARS.finditer(text, p + 1):
        j = match.start()
        if live[j] != "1":
            continue
        c = text[j]
        if c == "{":
            depth += 1
        elif c == "}":
            if depth:
                depth -= 1
            elif commas or ("0" not in live[p + 1 : j] and _is_range(text[p + 1 : j])):
                return j
        elif not depth:
            commas += 1
    return -1


def _top_level_commas(text: str, live: str, p: int, q: int) -> list:
    cuts, depth = [], 0
    for match in _BRACE_CHARS.finditer(text, p + 1, q):
        j = match.start()
        if live[j] != "1":
            continue
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth = max(0, depth - 1)
        elif not depth:
            cuts.append(j)
    return cuts


def _range_words(body: str):
    """The words of a {x..y[..step]} body, or None. Counted before built."""
    if not _is_range(body):
        return None
    a, b, inc = _RANGE.fullmatch(body).groups()
    numeric = bool(_NUMBER.match(a))
    step = 1 if inc is None else (abs(int(inc)) or 1)  # step 0 is 1
    x, y = (int(a), int(b)) if numeric else (ord(a), ord(b))
    count = abs(y - x) // step + 1
    if count > MAX_BRACE_WORDS:
        raise BraceOverflow
    pad = max(len(a.lstrip("+-")), len(b.lstrip("+-"))) if numeric and (_PADDED.match(a) or _PADDED.match(b)) else 0
    delta = step if y >= x else -step
    out = []
    for k in range(count):
        v = x + k * delta
        if not numeric:
            out.append(chr(v))
        elif pad:
            out.append(("-" if v < 0 else "") + str(abs(v)).zfill(pad))
        else:
            out.append(str(v))
    return out


def _brace_parts(text: str, live: str, p: int):
    """(q, [(text, live)]) for the brace expression whose { is at `p` and
    whose } is at q, or None when that { opens no expression."""
    if live[p] != "1" or (p == 0 and text[1:2] == "}" and live[1:2] == "1"):
        return None  # quoted or escaped, or a leading {} (text)
    q = _closing_brace(text, live, p)
    if q < 0:
        return None
    cuts = _top_level_commas(text, live, p, q)
    if cuts:
        bounds = [p, *cuts, q]
        return q, [(text[bounds[k] + 1 : bounds[k + 1]], live[bounds[k] + 1 : bounds[k + 1]]) for k in range(len(bounds) - 1)]
    seq = _range_words(text[p + 1 : q]) if "0" not in live[p + 1 : q] else None
    return (q, [(v, "0" * len(v)) for v in seq]) if seq else None


def _expand_from(text: str, live: str, steps: int) -> list:
    if steps > MAX_BRACE_STEPS:
        raise BraceOverflow
    p = text.find("{")
    while p >= 0:
        found = _brace_parts(text, live, p)
        if found:
            q, parts = found
            out: list = []
            for part, part_live in parts:
                out.extend(_expand_from(text[:p] + part + text[q + 1 :], live[:p] + part_live + live[q + 1 :], steps + 1))
                if len(out) > MAX_BRACE_WORDS:
                    raise BraceOverflow
            return out
        p = text.find("{", p + 1)
    return [text]


# expand_braces results by (text, live), BraceOverflow as _OVERFLOW: a word is
# expanded for the stage that holds it and again for the words its
# substitution prints, and once more in the second reading.
_EXPANSIONS: dict = {}
_OVERFLOW = object()


def expand_braces(text: str, live: str) -> list:
    """The words Bash makes of one word by brace expansion: {a,b}, nested
    lists, prefix and suffix, products, and {x..y[..step]} ranges. A quoted,
    escaped or ${...} brace (`live` "0"), an unterminated one, {a} and an
    invalid range stay text. An empty result is dropped unless the word had
    a literal char. Raises BraceOverflow past MAX_BRACE_WORDS words,
    MAX_BRACE_STEPS steps or MAX_LIVE_BRACES live braces."""
    if "{" not in text:
        return [text] if text else []
    key = (text, live)
    found = _EXPANSIONS.get(key)
    if found is None:
        live_braces = sum(1 for m in re.finditer(r"\{", text) if live[m.start()] == "1")
        try:
            if live_braces > MAX_LIVE_BRACES:
                raise BraceOverflow
            out = _expand_from(text, live, 0)
            found = tuple(out if "0" in live else [w for w in out if w])
        except BraceOverflow:
            found = _OVERFLOW
        _EXPANSIONS[key] = found
    if found is _OVERFLOW:
        raise BraceOverflow
    return list(found)


# The words of heredoc text: a body is text, so only blanks and punctuation split it.
_BODY_SPLIT = re.compile(r"[\s'\"`;|&<>(){}]+")


def output_words(script: Script, level: int, literal=frozenset(), budget=None) -> list:
    """The words a substitution can print, and so add to the word it sits
    in: every word in it, in the substitutions nested in it, and in its
    heredoc bodies (not those in `literal`, which no command receives). With
    `budget` (a one-item list), Bash words are brace-expanded and the words
    added are taken from it; past it BraceOverflow."""
    if level > MAX_DEPTH:
        raise NestingLimit
    out: list = []
    for token in script.tokens:
        if isinstance(token, str):
            continue
        if isinstance(token, Heredoc):
            if token not in literal:
                out.extend(w for w in _BODY_SPLIT.split(token.body.replace("\\", "/")) if w)
            continue
        if token.text:
            texts = expand_braces(token.text, token.live) if budget is not None and script.bash else [token.text]
            if budget is not None:
                budget[0] -= max(0, len(texts) - 1)
                if budget[0] < 0:
                    raise BraceOverflow
            out.extend(texts)
        for inner in token.subs:
            out.extend(output_words(inner, level + 1, literal, budget))
    return out


# ---------------------------------------------------------------------------
# Heredoc bodies no command runs
# ---------------------------------------------------------------------------

# Words that can open a stage before the command it runs, and the wrappers
# that run the next word, with the flags of each that take a value.
_START_KEYWORDS = frozenset({"if", "then", "else", "elif", "fi", "do", "done", "while", "until", "!"})
_START_WRAPPERS = {
    "sudo": frozenset({"-u", "-g", "-C", "-D", "-h", "-p", "-U", "-r", "-t"}),
    "env": frozenset({"-u", "-C", "-S"}),
    "time": frozenset({"-f", "-o"}),
    "nice": frozenset({"-n"}),
    "nohup": frozenset(),
}


def command_start(words: list) -> int:
    """The index in `words` (texts of one stage) of the command the stage
    runs: keywords, VAR=value and wrappers skipped (the installer's list;
    secret_dump_guard._core adds command/exec/builtin)."""
    i = 0
    while i < len(words):
        word = words[i]
        if word in _START_KEYWORDS or _ASSIGNMENT_WORD.match(word):
            i += 1
            continue
        value_flags = _START_WRAPPERS.get(command_name(word))
        if value_flags is None:
            break
        i += 1
        while i < len(words) and (words[i].startswith("-") or _ASSIGNMENT_WORD.match(words[i])):
            i += 2 if words[i] in value_flags else 1
    return i


def prose_values(words: list) -> set:
    """Indexes in `words` (texts of one stage) that hold a git/gh message
    value: the word after a message flag, or a --flag=<value> word."""
    start = command_start(words)
    if start >= len(words) or command_name(words[start]) not in ("git", "gh"):
        return set()
    out = set()
    for k in range(start + 1, len(words)):
        if words[k] in PROSE_FLAGS:
            out.add(k + 1)
        elif any(f.startswith("--") and words[k].startswith(f + "=") for f in PROSE_FLAGS):
            out.add(k)
    return out


def _reads_stdin(args: list, flags: tuple) -> bool:
    """`args` pass `-` (stdin) to one of `flags`, as `-F -` or `--file=-`."""
    return any(
        (a in flags and args[k + 1 : k + 2] == ["-"]) or any(a == f"{f}=-" for f in flags)
        for k, a in enumerate(args)
    )


def _stores_input(stage: Stage) -> bool:
    """A stage at the top of the command that stores the text it receives
    and runs none of it: tee to a file, git commit -F -, gh ... --body-file -."""
    if any(w.subs for w in (*stage.words, *stage.redirs)):
        return False
    words = [w.text for w in stage.words]
    runner, args = (command_name(words[0]) if words else ""), words[1:]
    if runner == "tee":
        return any(a and not a.startswith("-") for a in args)
    if runner not in _STDIN_FILE_FLAGS or (runner == "git" and "commit" not in args):
        return False
    return _reads_stdin(args, _STDIN_FILE_FLAGS[runner])


def _bare_cat(stage: Stage) -> bool:
    """A lone `cat` (or `cat -`) with nothing else in the stage."""
    return (
        bool(stage.words)
        and command_name(stage.words[0].text) == "cat"
        and all(not w.subs and (k == 0 or w.text == "-") for k, w in enumerate(stage.words))
        and not stage.redirs
    )


def literal_heredocs(script: Script, context: str = "top", out=None) -> set:
    """The heredocs in a parsed Bash script whose body is literal text that
    no command runs or turns into arguments: a quoted delimiter, a
    terminator line, and an inert receiver alone in its pipeline:
      - at the top of the command: tee to a file, git commit -F -,
        gh --body-file - (a bare `cat` prints the body, so it is scanned);
      - in a substitution that is a git/gh message value and holds nothing
        but that cat: git commit -m "$(cat <<'EOF' ... EOF)".
    Every other receiver keeps the body scanned."""
    out = set() if out is None else out
    found = pipelines(script)
    for pipeline in found:
        for stage in pipeline:
            prose = prose_values([w.text for w in stage.words])
            for k, w in enumerate(stage.words):
                for inner in w.subs:
                    literal_heredocs(inner, "prose" if k in prose else "args", out)
            for w in stage.redirs:
                for inner in w.subs:
                    literal_heredocs(inner, "args", out)
        if len(pipeline) != 1:
            continue
        stage = pipeline[0]
        inert = _stores_input(stage) if context == "top" else context == "prose" and len(found) == 1 and _bare_cat(stage)
        if inert:
            out.update(h for h in stage.heredocs if h.quoted and h.terminated)
    return out


def deny_on_error(guard: str, exc: BaseException) -> None:
    """Fail closed: deny when a guard cannot finish its check, naming the
    guard and the exception type. Only the type is printed; its message can
    carry the configured vault path."""
    deny(
        f"{guard} could not complete its check "
        f"({type(exc).__name__}); denying rather than allowing an "
        f"unchecked call. See .claude/hooks/{guard}.py."
    )


# Input caps. They deny input that could run a guard past its 5 s timeout,
# with a reason that names the limit. MAX_COMMAND_CHARS, MAX_LIVE_BRACES and
# MAX_BRACE_WORDS are the values of the ember-2-installer guards (that
# repo's .claude/hooks/hook-utils.cjs).
# A Bash or PowerShell command longer than this is denied before parsing.
MAX_COMMAND_CHARS = 16384
# A path or pattern field (PATH_FIELDS) longer than this is denied before it
# is checked: brace expansion and path resolution grow with its length.
MAX_PATH_CHARS = 4096
# Brace expansion (env_guard.py): `{` in one pattern, and alternatives one
# pattern expands to. Expansion is exponential in the brace sets.
MAX_LIVE_BRACES = 256
MAX_BRACE_WORDS = 256
SHELL_TOOLS = frozenset({"Bash", "PowerShell"})
# The path and pattern fields the guards check, by tool (vault_guard.py
# checks exactly these; env_guard.py maps the same fields to its checks).
PATH_FIELDS = {
    "Read": ("file_path",),
    "Edit": ("file_path",),
    "Write": ("file_path",),
    "Grep": ("path", "glob"),
    "Glob": ("path", "pattern"),
}


class InputLimit(Exception):
    """Input past a guard's cap. run_hook denies with str(exc) as the reason,
    so the message must be fixed text written by the guard, never built from
    the input (which can hold a vault path)."""


def _check_caps(guard: str, payload) -> None:
    """Raise InputLimit when a shell command is longer than
    MAX_COMMAND_CHARS or a path or pattern field is longer than
    MAX_PATH_CHARS. The reason holds only lengths and limits."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tool_input"), dict):
        return
    tool, tool_input = payload.get("tool_name"), payload["tool_input"]
    if tool in SHELL_TOOLS:
        fields, limit, what = ("command",), MAX_COMMAND_CHARS, "command"
    else:
        fields, limit, what = PATH_FIELDS.get(tool, ()), MAX_PATH_CHARS, "path or pattern"
    for field in fields:
        value = tool_input.get(field)
        if isinstance(value, str) and len(value) > limit:
            raise InputLimit(
                f"{guard}: this {what} is {len(value)} characters, over the "
                f"{limit} limit, so it is denied rather than checked against "
                f"the hook timeout. Split it into smaller calls or write the "
                f"content to a file. See .claude/hooks/hook_utils.py."
            )


def run_hook(guard: str, evaluate, reason) -> None:
    """One guard run: hook JSON on stdin, a deny on stdout or nothing. Exit
    code 0, or 2 (which blocks the call) when the deny cannot be written.

      - An unparseable payload is allowed, with a note on stderr naming
        only the exception type. The harness always sends JSON, and denying
        here would block every call and lock the session out of fixing the
        hook.
      - Input past a cap (_check_caps) is denied before `evaluate`.
      - `evaluate(payload)` returns a target (deny with `reason(target)`)
        or None (allow). An InputLimit it raises is a deny with its message.
      - Anything else raised in the check, SystemExit and KeyboardInterrupt
        included, is a deny naming the guard and the exception type
        (deny_on_error).

    Fail closed in two layers: this gives a named reason, and the guards'
    settings entries set "onFailure": "block", which covers what no code
    here can catch (a timeout, an interpreter that does not start, an
    import error before this runs).
    """
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        print(f"[{guard}] unparseable hook payload, allowing ({type(exc).__name__})", file=sys.stderr)
        return
    try:
        try:
            _check_caps(guard, payload)
            target = evaluate(payload)
            message = reason(target) if target else None
        except InputLimit as exc:
            message = str(exc)
        except BaseException as exc:
            deny_on_error(guard, exc)
            return
        if message:
            deny(message)
    except BaseException:
        # The deny could not be written (a closed or failing stdout): exit
        # code 2 is the harness's own block signal.
        sys.exit(2)


def deny(reason: str) -> None:
    """Print a PreToolUse deny decision with `reason`."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
