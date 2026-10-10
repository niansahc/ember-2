r"""
tests/conftest.py

Session-scoped vault isolation and rate-limiter fixtures.

Ensures ALL tests run against a temporary test vault, never the live
vault (C:\EmberVault). Two layers:

  1. At conftest import, before any `src` import, PRIVATE_VAULT_PATH is
     pointed at a throwaway vault, so the environment fallback can never
     resolve the live vault in this process. Importing src reads no .env
     (load_env_file() runs only in process entrypoints).
  2. For the session, the runtime override in src.core.config
     (set_vault_path_override / clear_vault_path_override) points at a
     fresh vault in pytest's tmp area.

When the override is cleared (session teardown, or a test that clears it
on purpose) resolution falls back to the layer-1 throwaway vault.
"""

import atexit
import contextlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Process environment isolation, applied at conftest import
#
# Everything here runs before the first `src` import. A session fixture would
# be too late: fixtures run after collection, and collection is when
# config.py, the app, and its LLMAdapter singletons are first imported.
#
# Vault. When the runtime override is None -- during collection, after session
# teardown, in tests that clear it on purpose (test_vault_swap.py), and after
# every importlib.reload(src.core.config) -- get_private_vault_path() falls
# back to PRIVATE_VAULT_PATH. Filled from .env (config.py loaded it at import
# until loading moved to the process entrypoints) or the shell, that was the
# live vault, and app import during collection wrote
# <live vault>/system/nature_version.txt on every pytest process.
# PRIVATE_VAULT_PATH is therefore pinned to a throwaway
# vault owned by this process, and the labelled paths the swap endpoint reads
# (VAULT_PATH_*) are removed. It is pinned rather than cleared: cleared, the
# override-cleared paths would raise instead of resolving somewhere safe.
#
# Config env vars (issue #195). config.py getters re-read os.getenv() on every
# call, so any value in os.environ leaks into whichever test runs next. Values
# came from two places:
#   - .env, through config.py's import-time load_dotenv(), which also re-ran
#     on every importlib.reload(src.core.config). Closed at the source:
#     config.py no longer reads .env on import; only process entrypoints
#     call load_env_file() (src/api/asgi.py, and scripts/tools under their
#     __main__ guard), so collection and reloads leave os.environ alone.
#   - The ambient shell. _LEAK_PRONE_ENV_VARS is cleared. Clearing before the
#     `ollama` package is imported also covers plain OLLAMA_HOST, which that
#     package binds once at import (see src/llm/adapter.py::_client_for_host).
# A test that wants a value sets it with monkeypatch/patch.dict; teardown
# lands back on "absent", never on a live value.
#
# Out of scope: get_ember_api_key()/get_provider_api_key() read the OS keyring
# before the env var, so clearing ANTHROPIC_API_KEY does not stop a test from
# reaching a key stored in Credential Manager.
# ---------------------------------------------------------------------------

# Explicit, audited list -- not runtime-discovered -- matching
# _RESOLVER_BINDING_MODULES' own philosophy below: the list is the audit, and
# a variable added to it should be a deliberate act. Enumerated from every
# os.getenv()/os.environ call site in src/. The vault variables are handled
# separately below.
_LEAK_PRONE_ENV_VARS: tuple[str, ...] = (
    # src/core/config.py
    "EMBER_DEV_MODE",
    "EMBER_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "EMBER_EMBED_MODEL",
    "TIER_RECENCY_HALFLIFE_DAYS",
    "TIER_ACCESS_CEILING",
    "TIER_HOT_THRESHOLD",
    "TIER_WARM_THRESHOLD",
    "STATE_STALENESS_DAYS",
    "RETRIEVAL_MIN_RAW_SCORE",
    "INTENT_CLASSIFIER_TIMEOUT_MS",
    "EMBER_DEBUG",
    "EMBER_CLASSIFIER_TELEMETRY",
    "EMBER_VISION_MODEL",
    "EMBER_GENERATION_OLLAMA_HOST",
    "EMBER_AUXILIARY_MODEL",
    "OLLAMA_HOST",
    "EMBER_MODEL",
    "EMBER_HOST",
    # src/retrieval/vector_index.py
    "MAX_INDEX_SIZE_MB",
    # src/safety/deviation_detector.py
    "EMBER_DEVIATION_DETECTION",
    "EMBER_DEVIATION_ENTROPY_THRESHOLD",
    "EMBER_DEVIATION_JACCARD_THRESHOLD",
    # src/state/state_resolver.py
    "EMBER_STATE_DEBUG",
)

_TEST_VAULT_SUBDIRS = (
    "memory/conversation",
    "memory/journal",
    "memory/reflection",
    "memory/state",
    "memory/ingested",
    "memory/archive",
    "memory/session",
    "embeddings",
    "imports",
)


def _make_vault_tree(root: Path) -> None:
    """Create the minimum directory structure services expect in a vault."""
    for subdir in _TEST_VAULT_SUBDIRS:
        (root / subdir).mkdir(parents=True, exist_ok=True)


# One throwaway root per pytest process. Daemon threads and the app's log
# handler may still hold files at exit, so removal is best-effort.
_PROCESS_TMP_ROOT = Path(tempfile.mkdtemp(prefix="ember-test-"))
atexit.register(shutil.rmtree, str(_PROCESS_TMP_ROOT), True)

# src/api/main.py attaches a rotating file handler at import; keep test runs
# out of the user's real application log.
os.environ["EMBER_LOG_PATH"] = str(_PROCESS_TMP_ROOT / "logs" / "ember-api.log")

_ENV_FALLBACK_VAULT = _PROCESS_TMP_ROOT / "vault"
_make_vault_tree(_ENV_FALLBACK_VAULT)
os.environ["PRIVATE_VAULT_PATH"] = str(_ENV_FALLBACK_VAULT)

for _key in (*_LEAK_PRONE_ENV_VARS, "VAULT_PATH_LIVE", "VAULT_PATH_DEMO", "VAULT_PATH_TEST"):
    os.environ.pop(_key, None)

import pytest  # noqa: E402

from src.core.config import set_vault_path_override, clear_vault_path_override
from src.context.render_window import (
    rendered_memory_window,
    rendered_reflection_window,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_fresh_python(
    args: list[str], vault: Path, timeout: int = 300
) -> subprocess.CompletedProcess:
    """Run `python <args>` in a fresh interpreter from the repo root.

    PRIVATE_VAULT_PATH is set to `vault`. Importing src reads no .env, and an
    entrypoint's load_env_file() (override=False) keeps that value over
    .env, so a probe never reaches a real vault. PYTEST_ADDOPTS is dropped so
    a child pytest is not steered by the parent's options.
    """
    env = dict(os.environ)
    env["PRIVATE_VAULT_PATH"] = str(vault)
    env.pop("PYTEST_ADDOPTS", None)
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=timeout,
    )


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

    Refuses to start if PRIVATE_VAULT_PATH no longer names the throwaway
    vault set at conftest import: something during collection (a test
    module, a plugin, a load_dotenv(override=True)) moved the fallback, and
    every override-cleared window would then resolve wherever it now points.
    """
    if os.environ.get("PRIVATE_VAULT_PATH") != str(_ENV_FALLBACK_VAULT):
        raise RuntimeError(
            "PRIVATE_VAULT_PATH changed during test collection; it no longer "
            "names the throwaway vault set by tests/conftest.py. Refusing to "
            "run: override-cleared code paths could reach a real vault."
        )

    test_vault = tmp_path_factory.mktemp("test_vault")

    # Create the minimum directory structure needed by services that
    # call get_private_vault_path() and expect subdirectories to exist.
    _make_vault_tree(test_vault)

    set_vault_path_override(str(test_vault), "test")

    yield test_vault

    clear_vault_path_override()


@pytest.fixture(scope="session")
def env_fallback_vault() -> Path:
    """The throwaway vault PRIVATE_VAULT_PATH names for this whole process.

    What get_private_vault_path() resolves whenever the override is cleared.
    """
    return _ENV_FALLBACK_VAULT



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
#      cleared. Resolution then fell through to PRIVATE_VAULT_PATH, which was
#      the real vault from .env at the time (it is now pinned to a throwaway
#      vault at conftest import), so every later test that wrote without its
#      own override wrote into it, against this module's own promise above.
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
    "src.memory.resolve_memory",
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

    Collection has normally imported most of this graph already, before any
    session fixture runs. Those imports are safe because PRIVATE_VAULT_PATH
    is pinned to a throwaway vault at conftest import; this fixture does not
    provide that protection. It depends on isolate_to_test_vault so that any
    module first imported here resolves against the session test vault.

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


# ---------------------------------------------------------------------------
# Delivery accounting (issue #227)
# ---------------------------------------------------------------------------

def deliver_packet(packet) -> int:
    """Render a packet the way the prompt does, then commit the stats write.

    build_context no longer writes retrieval stats: it arms a recorder and
    the adapter fires it once the prompt is final, against the slice the
    prompt rendered. Tests that need a delivery to have happened have to
    say so, and this is the one place that says it -- four copies of the
    same two lines would drift apart the first time the slice changes.

    The slice itself is no longer restated here. It was `memory_slice=4` and
    `reflection_slice=1` as default arguments, which is a copy of the shipped
    numbers wearing a parameter's clothes: nothing passed anything else, and the
    copy would have gone stale silently. Both windows now come from
    src/context/render_window.py, which the prompt builder and the trace harness
    also use.
    """
    render_packet(packet)
    return packet.commit_delivery()


def render_packet(packet) -> None:
    """The render half of deliver_packet, without the commit.

    For tests that let the adapter fire the commit (or withhold it) and need
    the render to have happened exactly as the prompt does it.
    """
    packet.begin_render()
    profile, other = rendered_memory_window(packet.memory_items)
    packet.record_rendered(profile + other)
    packet.record_rendered(rendered_reflection_window(packet.reflection_items))


def sse_events(text: str) -> list:
    """Parse an SSE body into its `data:` payloads: JSON objects and "[DONE]"."""
    import json

    out: list = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            out.append("[DONE]" if payload == "[DONE]" else json.loads(payload))
    return out


def vault_records(vault, memory_type: str) -> list[dict]:
    """Every canonical record of one type in a test vault, oldest first."""
    import json

    folder = Path(vault) / "memory" / memory_type
    if not folder.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def memory_db_ids(vault) -> set[str]:
    """Ids of the rows in a test vault's memory.db (empty when there is none)."""
    import sqlite3

    db = Path(vault) / "embeddings" / "memory.db"
    if not db.exists():
        return set()
    conn = sqlite3.connect(str(db))
    try:
        return {row[0] for row in conn.execute("SELECT id FROM vectors")}
    finally:
        conn.close()


def run_bound_inline(target, args=(), vault=None, name=None):
    """Drop-in for spawn_vault_bound_thread that runs the target inline,
    bound to the same vault, so index jobs finish before assertions run."""
    from src.core.config import vault_binding

    with vault_binding(vault):
        target(*args)


def synthetic_packet():
    """A one-record synthetic ContextPacket. No vault content."""
    from src.context.models import ContextItem, ContextPacket

    item = ContextItem(
        id="fixture-1",
        content="A synthetic fixture record with enough content to pass filters.",
        source="conversation",
        item_type="conversation",
        memory_type="conversation",
        score=0.6,
        timestamp="2026-03-15T10-00-00",
    )
    return ContextPacket(user_message="hello there", memory_items=[item])


# ---------------------------------------------------------------------------
# Query-embedding stubs (ADR-044 patch-path defect)
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def stub_both_embed_bindings(vector):
    """Patch BOTH embedding bindings a build_context test depends on.

    There are two, and patching one is the defect this helper exists to make
    impossible. `semantic_search.embed_text` is the one everybody reaches for;
    `ContextRetriever.retrieve` computes the query embedding through
    `src.retrieval.embed_memory.embed_text` and passes it down. Patching only
    the first leaves the stored records stubbed and the QUERY vector real, so
    the search runs at a measured cosine of 0.0052 against a flat fixture
    vector -- every record a total non-match.

    That was invisible until ADR-044 removed `memory_type_adjustment` and
    `source_quality_adjustment` from `semantic_search`. Those two contributed up
    to +0.40 of query-independent lift, enough to carry a 0.0052-cosine record
    across `_apply_type_gate`'s 0.25 similarity floor. One test then failed
    outright and six more turned out to have been green over an empty packet,
    asserting things ("no stats write", "records nothing",
    `commit_delivery() == 0`) that are trivially true of zero delivered items.

    Promoted here from five copies. Two of those copies already carried this
    explanation and the three that needed it did not have it -- a fix
    documented in one file does not reach another by being correct. If a third
    binding ever appears, it is added once.

    Callers that assert an ABSENCE still need their own positive precondition
    that the thing could have happened; this helper makes retrieval work, it
    does not make a vacuous assertion non-vacuous.
    """
    with patch("src.retrieval.semantic_search.embed_text", return_value=vector), \
         patch("src.retrieval.embed_memory.embed_text", return_value=vector):
        yield vector
