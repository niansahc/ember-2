"""
.claude/hooks/env_file.py

One .env value reader for the hooks that need one (post_commit_eval.py,
vault_guard.py), so they parse .env the same way. It never prints a
value.

Hooks run as bare subprocesses, so .env has not been loaded into the
environment. The process environment wins; otherwise the value comes from
python-dotenv (the venv has it), with a minimal KEY=VALUE parser as a
fallback so a hook never hard-depends on an import being present.
"""

import os
from pathlib import Path


def read_env_value(key: str, env_path: Path) -> str | None:
    """Return `key` from the process env, falling back to `env_path`."""
    value = os.environ.get(key)
    if value:
        return value.strip() or None

    try:
        from dotenv import dotenv_values

        return (dotenv_values(env_path).get(key) or "").strip() or None
    except Exception:
        pass

    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, val = line.partition("=")
            if name.strip() == key:
                return val.strip().strip('"').strip("'") or None
    except Exception:
        pass

    return None
