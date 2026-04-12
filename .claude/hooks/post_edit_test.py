"""
.claude/hooks/post_edit_test.py

PostToolUse hook for Edit|Write — auto-runs pytest after code edits.
Only triggers when the edited file is a Python file (*.py). Non-Python
edits (CLAUDE.md, YAML, JSON, etc.) are skipped silently.

Reads tool_input JSON from stdin. Runs the test suite and prints a
one-line summary. Exit code is always 0 so the hook never blocks
Claude Code's flow — a failing test is reported but does not prevent
the next edit.
"""

import json
import subprocess
import sys


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    file_path = data.get("tool_input", {}).get("file_path", "")
    if not file_path or not file_path.endswith(".py"):
        return

    # Only run tests when src/ files change — not tests, docs, scripts, or tools.
    # Normalise path separators for cross-platform matching.
    normalized = file_path.replace("\\", "/")
    if "/src/" not in normalized:
        return

    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--tb=line", "-q"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        # Print the last 3 lines (summary) so the hook output is concise.
        lines = result.stdout.strip().splitlines()
        for line in lines[-3:]:
            print(line)
        if result.returncode != 0:
            print("[HOOK] Tests failed — review before committing.")
    except subprocess.TimeoutExpired:
        print("[HOOK] pytest timed out after 180s.")
    except Exception as exc:
        print(f"[HOOK] pytest failed to run: {exc}")


if __name__ == "__main__":
    main()
