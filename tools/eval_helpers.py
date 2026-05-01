"""
tools/eval_helpers.py

Shared helpers for eval tools: test vault isolation and post-run cleanup.

- swap_to_test_vault / restore_vault: switch the running API process to
  the test vault for eval isolation. Calls POST /v1/developer/vault/swap
  on the live API so the swap reaches the API process. Setting only an
  in-process Python global (the prior implementation) did NOT reach the
  API, which meant eval tools silently ran against the live vault. Bug
  fixed 2026-04-30.

- Privacy posture: swap_to_test_vault fails closed via sys.exit(1) on
  any path that would let the eval proceed against the live vault.
  This includes: missing VAULT_PATH_TEST env var, missing test-vault
  directory, and any swap-call failure (connection refused, 403
  dev-mode disabled, 400 unknown label, 5xx). Silent-return-None was
  the original 2026-04-30 leak: callers cannot distinguish "swap
  skipped" from "swap succeeded," so any None path is treated as a
  privacy regression and made fatal.

- run_cleanup: invoke the same logic as cleanup_test_artifacts.py
  --confirm to archive eval artifacts from the active vault silently.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


_DEFAULT_API_BASE = "http://localhost:8000"
_VAULT_SWAP_PATH = "/v1/developer/vault/swap"


def _api_base() -> str:
    """Resolve the eval target API base URL. EMBER_API_BASE overrides
    the localhost default for non-default ports or remote eval setups."""
    return os.getenv("EMBER_API_BASE", _DEFAULT_API_BASE).rstrip("/")


def _swap_headers() -> dict:
    """Build headers for the vault swap POST. Includes Authorization
    when EMBER_API_KEY is configured."""
    from src.core.config import get_ember_api_key

    headers = {"Content-Type": "application/json"}
    api_key = get_ember_api_key()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def swap_to_test_vault() -> str:
    """Switch the running API to the test vault for eval isolation.

    Returns "test" on successful swap. On any failure path - missing
    VAULT_PATH_TEST env var, missing test-vault directory, or a failed
    swap call - prints a fatal message and exits the process. This
    prevents the caller from proceeding against the live vault.

    Missing env / missing dir are configuration errors, not "graceful
    skip" conditions. The PR #37 design intent is that no eval ever
    runs against the live vault by accident; any None return from this
    helper would re-open that hole.
    """
    test_path = os.getenv("VAULT_PATH_TEST")
    if not test_path:
        print(
            "FATAL: VAULT_PATH_TEST is not set. Eval tools require an "
            "explicit test vault to fail closed against the live vault. "
            "Set VAULT_PATH_TEST in .env and export it into the shell "
            "before invoking eval (load_dotenv runs inside the API, not "
            "in eval-tool subprocesses)."
        )
        sys.exit(1)

    resolved = Path(test_path).resolve()
    if not resolved.is_dir():
        print(
            f"FATAL: VAULT_PATH_TEST ({resolved}) does not exist or is "
            "not a directory. Refusing to proceed against the live vault. "
            "Create the test vault directory or correct VAULT_PATH_TEST."
        )
        sys.exit(1)

    url = f"{_api_base()}{_VAULT_SWAP_PATH}"
    try:
        response = httpx.post(
            url,
            json={"vault_label": "test"},
            headers=_swap_headers(),
            timeout=30.0,
        )
        response.raise_for_status()
    except Exception as exc:
        print(f"FATAL: vault swap to test failed: {exc}")
        print("Refusing to proceed against live vault. Aborting.")
        sys.exit(1)

    print(f"Vault swap (API): {response.json()}")
    return "test"


def restore_vault(previous_path: str | None) -> None:
    """Restore the API to the default vault after eval.

    The previous_path argument is retained for caller compatibility but
    is no longer needed: the API endpoint reverts to PRIVATE_VAULT_PATH
    when called with vault_label="default". A None argument indicates
    swap_to_test_vault was skipped (no swap occurred), so no restore
    call is fired.

    Best-effort: prints a WARNING on failure but does not exit. The eval
    has already finished by the time restore runs; manual restore via
    POST /v1/developer/vault/swap {"vault_label": "default"} or an API
    restart is documented in the failure message.
    """
    if previous_path is None:
        return

    url = f"{_api_base()}{_VAULT_SWAP_PATH}"
    try:
        response = httpx.post(
            url,
            json={"vault_label": "default"},
            headers=_swap_headers(),
            timeout=30.0,
        )
        response.raise_for_status()
        print(f"Vault restore (API): {response.json()}")
    except Exception as exc:
        print(
            f"WARNING: vault restore failed: {exc}. "
            "Manual restore: POST /v1/developer/vault/swap "
            "{'vault_label': 'default'} or restart the API."
        )


def run_cleanup() -> None:
    """Run artifact cleanup against the currently active vault.

    Calls the same scan_vault + archive_records logic as
    scripts/cleanup_test_artifacts.py --confirm. Silent: only prints
    if records were archived.
    """
    try:
        scripts_dir = REPO_ROOT / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))

        from cleanup_test_artifacts import scan_vault, archive_records
        from src.core.config import get_private_vault_path

        vault = get_private_vault_path()
        matches = scan_vault(vault)
        if matches:
            moved = archive_records(vault, matches)
            print(f"Cleaned up {moved} eval artifact(s) from vault.")
    except Exception as exc:
        print(f"WARNING: Cleanup failed (non-fatal): {exc}")
