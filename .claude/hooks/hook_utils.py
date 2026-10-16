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

Deliberately pathlib- and typing-free: PreToolUse guards start a fresh
Python process on every tool call, and those imports cost ~11 ms each time.
"""

import json
import os
import re
import sys


def project_dir() -> str:
    """CLAUDE_PROJECT_DIR, falling back to the checkout this file lives in
    (a manual run), so a hook behaves the same from any session cwd."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


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


def _checkout_root() -> str:
    """The checkout this file lives in (.claude/hooks/hook_utils.py ->
    root), whose src/ holds the shared resolver. Not project_dir(): tests
    point CLAUDE_PROJECT_DIR at a tmp project that has no src/."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def project_path(value: str) -> str:
    """A configured path (PRIVATE_VAULT_PATH, VAULT_PATH_TEST) as an
    absolute path, through src/core/vault_path.resolve_configured_path, the
    one rule the app uses. A relative value resolves against the project
    root, never against the hook process cwd, which follows the session's
    cd.

    The import is deferred to here so the hooks that import this module
    and never resolve a configured value (env_guard.py, and
    secret_dump_guard.py on commands that name no path) do not load it.
    src/__init__.py and src/core/__init__.py are empty and vault_path.py
    imports only os, so no app code loads."""
    root = _checkout_root()
    if root not in sys.path:
        sys.path.append(root)
    from src.core.vault_path import resolve_configured_path

    return resolve_configured_path(value, project_dir())


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
        with open(env_path, encoding="utf-8") as handle:
            for raw in handle.read().splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                name, _, val = line.partition("=")
                if name.strip() == key:
                    return val.strip().strip('"').strip("'") or None
    except Exception:
        pass

    return None


def deny(reason: str) -> None:
    """Print a PreToolUse deny decision with `reason`."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
