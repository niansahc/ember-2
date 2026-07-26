r"""
tests/conftest.py

Session-scoped vault isolation and rate-limiter fixtures.

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
def disable_rate_limiter():
    """Disable the shared slowapi limiter for the whole test session.

    The limiter is global (60/minute, keyed on remote address) and every
    TestClient request presents the same synthetic address, so the entire
    suite draws on one bucket. That makes independent test files couple
    through a shared global: adding an endpoint test anywhere can push an
    unrelated file over the limit and fail it with 429.

    That is not hypothetical. CI runs the suite in ~83s where local runs
    take ~14 minutes, so the bucket refills locally and does not in CI.
    Three new endpoint tests in test_vision_failure_path.py were enough to
    fail test_web_search_header.py in CI while the full suite passed
    locally, which is a false signal in the direction that matters least.

    Rate limiting is production behaviour worth its own targeted test
    (see tests/test_pin_service.py for the PIN attempt limiter, which
    manages its own state). It should not be an implicit, order-dependent
    budget shared across every test that touches the API.
    """
    from src.api.limiter import limiter

    previous = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = previous


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
