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


# ---------------------------------------------------------------------------
# Process-state isolation contract
#
# These suites share process-global state -- the config module's override
# globals, the write-block flag, the store and index caches, and the
# import-time bindings in every module that does
# `from src.core.config import get_private_vault_path`. Nothing restored that
# state between tests, so two defects lived here:
#
#   1. test_vault_toggle.py imported the app graph from inside a patch of
#      src.core.config.get_private_vault_path. `from x import y` copies the
#      reference, so the unpatch could not reach the copies: ten modules kept
#      a MagicMock resolver for the rest of the session, and the swap suites
#      failed with a write store resolved under a toggle test's tmp_path.
#
#   2. Twelve tests in test_vault_swap.py end with the session override
#      cleared. Resolution then falls through to PRIVATE_VAULT_PATH from .env
#      -- the real vault -- so every later test that wrote without its own
#      override wrote into it, against this module's own promise above.
#
# The three fixtures below are the contract. They are autouse so no test file
# can opt out, and so a file added later inherits them without knowing they
# exist.
# ---------------------------------------------------------------------------


# Modules that bind the resolver at import time and can therefore capture a
# patched Mock permanently. Kept explicit rather than discovered at runtime:
# the list is the audit, and a module added to it should be a deliberate act.
_RESOLVER_BINDING_MODULES = (
    "src.memory.write_memory",
    "src.memory.read_memory",
    "src.memory.search_memory",
    "src.memory.session",
    "src.memory.project",
    "src.memory.lodestone_service",
    "src.core.preferences",
    "src.retrieval.semantic_search",
    "src.tasks.task_service",
    "src.api.routes.ingest",
)


@pytest.fixture(scope="session", autouse=True)
def import_app_graph_before_any_patch(isolate_to_test_vault):
    """Import every capturable module once, before any test can patch.

    Depends on isolate_to_test_vault so the imports resolve against the test
    vault rather than the .env one.

    Imports the audited list explicitly rather than relying on what
    `src.api.main` happens to pull in. That distinction is load-bearing:
    src.core.preferences is NOT in the app's import graph, so importing the
    app alone left it exposed, and nested patches made it worse than the
    single-patch case. When a fixture patches config's resolver and then
    patches preferences' copy, the inner patch captures the already-mocked
    value as its "original" and restores *that* on exit -- so the Mock
    survives even though both patches unwound correctly.

    This removes the failure class rather than one instance of it: whichever
    test file happens to import a module first can no longer be the file whose
    patch context that module captures.
    """
    import importlib

    import src.api.main  # noqa: F401

    for module_name in _RESOLVER_BINDING_MODULES:
        importlib.import_module(module_name)


@pytest.fixture(autouse=True)
def restore_process_state():
    """Snapshot and restore process-global vault state around every test.

    Promoted from tests/test_vault_scoped_stores.py, where it guarded one file
    and every other file went unguarded. A test may move the override, set the
    write block, or populate the store cache; none of it outlives the test.
    """
    from src.core.config import (
        allow_vault_writes,
        block_vault_writes,
        clear_vault_path_override,
        get_vault_override,
        set_vault_path_override,
        vault_writes_blocked,
    )
    from src.retrieval.store_cache import clear_store_cache
    from src.retrieval.vector_index import clear_index_cache

    prior_path, prior_label = get_vault_override()
    prior_block = vault_writes_blocked()

    yield

    clear_store_cache()
    clear_index_cache()
    if prior_path is None:
        clear_vault_path_override()
    else:
        set_vault_path_override(prior_path, prior_label)
    if prior_block is None:
        allow_vault_writes()
    else:
        block_vault_writes(prior_block)


@pytest.fixture(autouse=True)
def fail_on_leaked_resolver_mock(request):
    """Fail a test that leaves a Mock bound where the resolver belongs.

    import_app_graph_before_any_patch prevents the import-order capture, but
    not every route to the same end: a test that reloads src.core.config while
    a patch is active rebinds the globals just as permanently. This turns that
    silent, session-wide contamination into an immediate failure naming the
    test that caused it.
    """
    yield

    import sys
    from unittest.mock import NonCallableMock

    leaked = []
    for module_name in _RESOLVER_BINDING_MODULES:
        module = sys.modules.get(module_name)
        if module is None:
            continue
        resolver = getattr(module, "get_private_vault_path", None)
        if isinstance(resolver, NonCallableMock) or type(resolver).__name__ in (
            "MagicMock", "Mock", "AsyncMock",
        ):
            leaked.append(module_name)

    assert not leaked, (
        f"{request.node.nodeid} left a mocked get_private_vault_path bound in "
        f"{leaked}. A `from src.core.config import get_private_vault_path` copy "
        "cannot be reached by unpatching, so this would persist for the rest of "
        "the session and silently redirect every later vault access. Import the "
        "module under test outside the patch context, or patch the attribute on "
        "the binding module instead of on src.core.config."
    )
