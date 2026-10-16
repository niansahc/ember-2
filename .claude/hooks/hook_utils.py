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
        from dotenv import dotenv_values

        return (dotenv_values(env_path).get(key) or "").strip() or None
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
    ` # comment`, `${VAR}` and `${VAR:-default}` resolved from the keys
    above it, then the process environment). The fallback must agree with
    dotenv: a different PRIVATE_VAULT_PATH points the vault guards at the
    wrong directory. A line this parser cannot read the way dotenv does (an
    unclosed quote, a quoted value followed by text, a reference left
    unresolved, such as a nested default) raises ValueError; the vault
    guards deny when a check raises, so no guess is made."""
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
            env = {**os.environ, **values}
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
