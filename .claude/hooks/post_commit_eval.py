"""
.claude/hooks/post_commit_eval.py

PostToolUse hook for Bash - runs retrieval eval after commits that
touch src/context/, src/retrieval/, or src/llm/.

Only fires when the Bash command contains "git commit" (not git status,
git log, etc.). Checks `git diff HEAD~1 --name-only` for changed files
and runs `python tools/eval_retrieval.py` if any match the trigger
paths. Silent no-op otherwise.

Vault isolation: tools/eval_retrieval.py does NOT self-isolate. It
imports ContextService in-process, which resolves the vault through
get_private_vault_path() -> PRIVATE_VAULT_PATH from .env. The API's
in-memory _vault_path_override lives in the uvicorn process and cannot
reach a subprocess, so without an explicit environment override this
hook would run 15 benchmark queries against the user's live personal
vault on every qualifying commit. CLAUDE.md requires all evals to run
against the test vault only.

This hook therefore forces PRIVATE_VAULT_PATH=VAULT_PATH_TEST for the
child process only. It never mutates its own environment or .env. The
override survives eval_retrieval.py's own .env load at startup
(load_env_file(), override=False), so a pre-existing environment
variable wins over the .env value.

Fails closed: if the test vault cannot be resolved, is not a directory,
or resolves to the same path as the live vault, the eval is skipped with
a printed reason rather than falling back to the live vault.

A relative VAULT_PATH_TEST or PRIVATE_VAULT_PATH resolves against the
project root (hook_utils.project_path), the root vault_guard.py resolves
PRIVATE_VAULT_PATH against, never against this process's cwd. Otherwise,
from a session cwd'd elsewhere, a relative live vault and an absolute
test vault naming the same directory compare unequal, and the eval runs
against the live vault.

Exit code is always 0 - a failing eval is reported but does not block.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

# Same directory; Python puts it on sys.path when this file runs as a
# script. Shared with vault_guard.py so both hooks parse .env one way and
# resolve a relative vault path against the same root.
from hook_utils import project_dir, project_path, read_env_value


TRIGGER_PATHS = ("src/context/", "src/retrieval/", "src/llm/")

# Anchor for every path and subprocess cwd below, so the hook behaves the
# same whichever directory the session has cd'd into.
REPO_ROOT = Path(project_dir())
ENV_PATH = REPO_ROOT / ".env"


def _configured_path(value: str) -> Path:
    """A configured vault path, relative values against the project root,
    then through realpath so two spellings of one directory compare equal."""
    return Path(project_path(value)).resolve()


def _resolve_test_vault() -> tuple[Path | None, str | None]:
    """Resolve the test vault, or return a reason it cannot be used.

    Returns (path, None) on success and (None, reason) on failure. Every
    failure path is a refusal, never a fallback to the live vault.
    """
    test_vault = read_env_value("VAULT_PATH_TEST", ENV_PATH)
    if not test_vault:
        return None, "VAULT_PATH_TEST is not set"

    test_path = _configured_path(test_vault)
    if not test_path.is_dir():
        # A missing directory would let SqliteVectorStore.__init__ mkdir a
        # new empty vault and report a meaningless all-FAIL eval.
        return None, "VAULT_PATH_TEST does not point at an existing directory"

    live_vault = read_env_value("PRIVATE_VAULT_PATH", ENV_PATH)
    if live_vault and _configured_path(live_vault) == test_path:
        return None, "VAULT_PATH_TEST resolves to the same path as PRIVATE_VAULT_PATH"

    return test_path, None


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
            cwd=REPO_ROOT,
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

    test_vault, reason = _resolve_test_vault()
    if test_vault is None:
        print(
            f"[HOOK] Skipping retrieval eval: {reason}. "
            "Refusing to run against the live vault (CLAUDE.md: evals are "
            "test-vault only). Set VAULT_PATH_TEST to re-enable this hook."
        )
        return

    print("[HOOK] Commit touched context/retrieval/llm - running retrieval eval...")
    print(f"[HOOK] Vault: {test_vault}")

    child_env = {**os.environ, "PRIVATE_VAULT_PATH": str(test_vault)}

    try:
        eval_result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tools" / "eval_retrieval.py")],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
            env=child_env,
        )
        lines = eval_result.stdout.strip().splitlines()
        for line in lines[-5:]:
            print(line)
        if eval_result.returncode != 0:
            print("[HOOK] Retrieval eval reported failures - review before pushing.")
    except subprocess.TimeoutExpired:
        print("[HOOK] eval_retrieval.py timed out after 120s.")
    except Exception as exc:
        print(f"[HOOK] eval_retrieval.py failed to run: {exc}")


if __name__ == "__main__":
    main()
