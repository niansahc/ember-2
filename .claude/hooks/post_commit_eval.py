"""
.claude/hooks/post_commit_eval.py

PostToolUse hook for Bash — runs retrieval eval after commits that
touch src/context/, src/retrieval/, or src/llm/.

Only fires when the Bash command contains "git commit" (not git status,
git log, etc.). Checks `git diff HEAD~1 --name-only` for changed files
and runs `python tools/eval_retrieval.py` if any match the trigger
paths. Silent no-op otherwise.

Exit code is always 0 — a failing eval is reported but does not block.
"""

import json
import subprocess
import sys


TRIGGER_PATHS = ("src/context/", "src/retrieval/", "src/llm/")


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    command = data.get("tool_input", {}).get("command", "")
    if "git commit" not in command:
        return

    try:
        result = subprocess.run(
            ["git", "diff", "HEAD~1", "--name-only"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        changed_files = result.stdout.strip().splitlines()
    except Exception:
        return

    if not any(
        f.replace("\\", "/").startswith(p) for f in changed_files for p in TRIGGER_PATHS
    ):
        return

    print("[HOOK] Commit touched context/retrieval/llm — running retrieval eval...")
    try:
        eval_result = subprocess.run(
            [sys.executable, "tools/eval_retrieval.py"],
            capture_output=True,
            text=True,
            timeout=120,
        )
        lines = eval_result.stdout.strip().splitlines()
        for line in lines[-5:]:
            print(line)
        if eval_result.returncode != 0:
            print("[HOOK] Retrieval eval reported failures — review before pushing.")
    except subprocess.TimeoutExpired:
        print("[HOOK] eval_retrieval.py timed out after 120s.")
    except Exception as exc:
        print(f"[HOOK] eval_retrieval.py failed to run: {exc}")


if __name__ == "__main__":
    main()
