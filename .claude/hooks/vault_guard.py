"""
.claude/hooks/vault_guard.py

PreToolUse hook for Edit|Write -- blocks edits to private_vault/ and to
.env files (.env, .env.local, .env.production, ...). .env.example is
allowed. Reads tool_input JSON from stdin. Returns a deny decision if the
file path matches a protected pattern; silent pass-through otherwise.

The .env check is names_env_file() from env_guard.py (same directory,
which Python puts on sys.path when this file runs as a script), so Edit/
Write and the read tools share one definition of "a .env file."
"""

import json
import sys

from env_guard import names_env_file


def _is_vault_path(file_path: str) -> bool:
    return "private_vault/" in file_path or "private_vault\\" in file_path


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return

    if _is_vault_path(file_path) or names_env_file(file_path):
        result = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    "Vault Privacy Rule: edits to private_vault/ and .env "
                    "files are blocked. See CLAUDE.md."
                ),
            }
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()
