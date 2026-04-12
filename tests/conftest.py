"""
tests/conftest.py

Session-scoped vault isolation fixture.

Ensures ALL tests run against a temporary test vault, never the live
vault (C:\EmberVault). The override uses the runtime mechanism in
src.core.config (set_vault_path_override / clear_vault_path_override)
which takes precedence over PRIVATE_VAULT_PATH in .env.

Cleanup is guaranteed by pytest's fixture teardown — even on failure,
KeyboardInterrupt, or crash, the override is cleared and the system
reverts to the .env vault path.
"""

import pytest
from pathlib import Path

from src.core.config import set_vault_path_override, clear_vault_path_override


@pytest.fixture(scope="session", autouse=True)
def isolate_to_test_vault(tmp_path_factory):
    """Redirect all vault access to a temporary test vault for the session.

    Creates a fresh vault directory structure in pytest's tmp area.
    Sets the runtime override at session start and clears it at session
    end. No test ever reads from or writes to the live vault.
    """
    test_vault = tmp_path_factory.mktemp("test_vault")

    # Create the minimum directory structure needed by services that
    # call get_private_vault_path() and expect subdirectories to exist.
    for subdir in (
        "memory/conversation",
        "memory/journal",
        "memory/reflection",
        "memory/state",
        "memory/ingested",
        "memory/archive",
        "memory/session",
        "embeddings",
        "imports",
    ):
        (test_vault / subdir).mkdir(parents=True, exist_ok=True)

    set_vault_path_override(str(test_vault), "test")

    yield test_vault

    clear_vault_path_override()
