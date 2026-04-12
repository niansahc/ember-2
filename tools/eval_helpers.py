"""
tools/eval_helpers.py

Shared helpers for eval tools: test vault isolation and post-run cleanup.

- swap_to_test_vault / restore_vault: switch PRIVATE_VAULT_PATH to the
  test vault at eval start, restore to live vault after. Keeps eval
  artifacts out of the live vault entirely.

- run_cleanup: invoke the same logic as cleanup_test_artifacts.py --confirm
  to archive eval artifacts from the active vault silently.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def swap_to_test_vault() -> str | None:
    """Switch to the test vault for eval isolation.

    Reads VAULT_PATH_TEST from .env. If set and the directory exists,
    sets the runtime vault path override and clears vector indexes.
    Returns the previous vault path string (for restore), or None if
    the swap was skipped (test vault not configured or missing).
    """
    test_path = os.getenv("VAULT_PATH_TEST")
    if not test_path:
        return None

    resolved = Path(test_path).resolve()
    if not resolved.is_dir():
        print(f"WARNING: VAULT_PATH_TEST ({resolved}) does not exist. Running against live vault.")
        return None

    from src.core.config import (
        get_private_vault_path,
        set_vault_path_override,
    )
    from src.retrieval.vector_index import clear_index_cache

    previous = str(get_private_vault_path())
    set_vault_path_override(str(resolved), "test")
    clear_index_cache()
    print(f"Vault swapped to test: {resolved}")
    return previous


def restore_vault(previous_path: str | None) -> None:
    """Restore the vault path after eval. If previous_path is None,
    just clears the override (reverts to .env default)."""
    from src.core.config import clear_vault_path_override
    from src.retrieval.vector_index import clear_index_cache

    clear_vault_path_override()
    clear_index_cache()
    if previous_path:
        print(f"Vault restored to: {previous_path}")


def run_cleanup() -> None:
    """Run artifact cleanup against the currently active vault.

    Calls the same scan_vault + archive_records logic as
    scripts/cleanup_test_artifacts.py --confirm. Silent — only prints
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
