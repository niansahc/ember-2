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
override survives eval_retrieval.py's own load_dotenv() because
python-dotenv defaults to override=False, so a pre-existing environment
variable wins over the .env value.

Fails closed: if the test vault cannot be resolved, is not a directory,
or resolves to the same path as the live vault, the eval is skipped with
a printed reason rather than falling back to the live vault.

Exit code is always 0 - a failing eval is reported but does not block.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


TRIGGER_PATHS = ("src/context/", "src/retrieval/", "src/llm/")

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"


def _read_env_value(key: str) -> str | None:
    """Return a value from the process env, falling back to a .env lookup.

    The hook runs as a bare subprocess, so .env has not been loaded. Try
    python-dotenv first (the venv has it) and fall back to a minimal
    parser so this hook never hard-depends on an import being present.
    """
    value = os.environ.get(key)
    if value:
        return value.strip() or None

    try:
        from dotenv import dotenv_values

        return (dotenv_values(ENV_PATH).get(key) or "").strip() or None
    except Exception:
        pass

    try:
        for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, val = line.partition("=")
            if name.strip() == key:
                return val.strip().strip('"').strip("'") or None
    except Exception:
        pass

    return None


def _resolve_test_vault() -> tuple[Path | None, str | None]:
    """Resolve the test vault, or return a reason it cannot be used.

    Returns (path, None) on success and (None, reason) on failure. Every
    failure path is a refusal, never a fallback to the live vault.
    """
    test_vault = _read_env_value("VAULT_PATH_TEST")
    if not test_vault:
        return None, "VAULT_PATH_TEST is not set"

    test_path = Path(test_vault).resolve()
    if not test_path.is_dir():
        # A missing directory would let SqliteVectorStore.__init__ mkdir a
        # new empty vault and report a meaningless all-FAIL eval.
        return None, "VAULT_PATH_TEST does not point at an existing directory"

    live_vault = _read_env_value("PRIVATE_VAULT_PATH")
    if live_vault and Path(live_vault).resolve() == test_path:
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
            [sys.executable, "tools/eval_retrieval.py"],
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
