"""
.claude/hooks/vault_guard.py

PreToolUse hook for Edit|Write — blocks edits to private_vault/ and .env.
Reads tool_input JSON from stdin. Returns a deny decision if the file
path matches a protected pattern; silent pass-through otherwise.
"""

import json
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path:
        return

    if "private_vault/" in file_path or "private_vault\\" in file_path or file_path.endswith(".env"):
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
