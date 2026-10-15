"""
.claude/hooks/hook_utils.py

Helpers shared by the hook scripts in this directory, so each rule has one
definition:

  - project_dir(): the project root every hook anchors on.
  - absolute_path() / project_path(): one resolution of a path string.
    project_path() is how every hook resolves a configured value
    (PRIVATE_VAULT_PATH, VAULT_PATH_TEST), so vault_guard.py,
    secret_dump_guard.py and post_commit_eval.py agree on which directory
    is the live vault.
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

# Path-form prefixes, matched after backslashes become slashes.
_DEVICE_PREFIX_RE = re.compile(r"^//[?.]/")  # \\?\C:\x, \\.\C:\x
_POSIX_DRIVE_RE = re.compile(r"^/(?:mnt/)?([a-z])(?=/|$)", re.IGNORECASE)  # /c/x, /mnt/c/x


def project_dir() -> str:
    """CLAUDE_PROJECT_DIR, falling back to the checkout this file lives in
    (a manual run), so a hook behaves the same from any session cwd."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def absolute_path(path: str, base: str) -> str:
    """`path` as an absolute, lexically normalised path, for every form an
    agent might write. Relative forms resolve against `base`."""
    s = _DEVICE_PREFIX_RE.sub("", path.replace("\\", "/"))
    s = os.path.expanduser(s)
    m = _POSIX_DRIVE_RE.match(s) if os.name == "nt" else None
    if m:
        s = f"{m.group(1)}:/{s[m.end():]}"
    # ntpath.join also resolves drive-relative C:x against `base` when it is
    # on the same drive (the only drive whose cwd the hook knows).
    return os.path.normpath(os.path.join(base, s))


def project_path(value: str) -> str:
    """A configured path (PRIVATE_VAULT_PATH, VAULT_PATH_TEST) as an
    absolute path. A relative value resolves against the project root, as
    the API, started from the project root, resolves it; never against the
    hook process cwd, which follows the session's cd."""
    return absolute_path(value, project_dir())


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
