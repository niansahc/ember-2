"""
.claude/hooks/hook_utils.py

Helpers shared by the hook scripts in this directory, so each rule has one
definition:

  - project_dir(): the project root every hook anchors on.
  - read_env_value(): one .env value reader (post_commit_eval.py,
    vault_guard.py). It never prints a value.
  - deny(): the PreToolUse deny decision the harness reads from stdout.

Deliberately pathlib- and typing-free: PreToolUse guards start a fresh
Python process on every tool call, and those imports cost ~11 ms each time.
"""

import json
import os


def project_dir() -> str:
    """CLAUDE_PROJECT_DIR, falling back to the checkout this file lives in
    (a manual run), so a hook behaves the same from any session cwd."""
    return os.environ.get("CLAUDE_PROJECT_DIR") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )


def read_env_value(key: str, env_path) -> str | None:
    """Return `key` from the process env, falling back to the .env file at
    `env_path`.

    Hooks run as bare subprocesses, so .env has not been loaded into the
    environment. The value comes from python-dotenv (the venv has it), with
    a minimal KEY=VALUE parser as a fallback so a hook never hard-depends on
    an import being present.
    """
    value = os.environ.get(key)
    if value:
        return value.strip() or None

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
