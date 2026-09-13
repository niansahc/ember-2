"""
tests/test_vault_bound_threads.py

Deferred writes stay in the vault their work was queued under (issue #144).

A vault swap mutates a process-global in src/core/config.py, and
get_private_vault_path() re-resolves on every call. Work that is queued under
one vault but runs later -- a daemon thread, a streaming response's
post-stream cleanup -- therefore used to resolve whichever vault was active
when it finally executed. A swap landing mid-turn could route a live-vault
turn's write into the test vault, or the reverse.

These tests pin the replacement behaviour: a binding is consulted before the
swap override, so bound work cannot observe a later swap. Thread ordering is
forced with Events rather than sleeps, so the swap provably lands between
spawn and execution.

All fixtures build synthetic vaults under tmp_path. No real vault data.
"""

import threading

import pytest

from src.core.config import (
    clear_vault_path_override,
    get_bound_vault,
    get_private_vault_path,
    get_vault_override,
    set_vault_path_override,
    spawn_vault_bound_thread,
    vault_binding,
)
from src.retrieval.store_cache import clear_store_cache


def make_vault(root):
    """Create the minimum vault directory structure under root."""
    for subdir in ("memory/conversation", "memory/journal", "memory/state", "embeddings"):
        (root / subdir).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(autouse=True)
def restore_vault_state():
    """Snapshot and restore vault override and store cache around each test.

    Mirrors tests/test_vault_scoped_stores.py: the session-scoped
    isolate_to_test_vault fixture sets one override for the whole run, and
    these tests move it deliberately.
    """
    prior_path, prior_label = get_vault_override()
    clear_store_cache()
    yield
    clear_store_cache()
    if prior_path is None:
        clear_vault_path_override()
    else:
        set_vault_path_override(prior_path, prior_label)


@pytest.fixture
def write_record(monkeypatch):
    """Return a callable that writes one synthetic conversation record into
    whatever vault is active at the moment it is called."""
    from src.memory import write_memory as write_memory_module

    monkeypatch.setattr(write_memory_module, "embed_text", lambda text: [0.1, 0.2, 0.3])

    def _write(text):
        return write_memory_module.write_memory(
            text=text,
            memory_type="conversation",
            source="test",
        )

    return _write


def _records_in(vault):
    return sorted((vault / "memory" / "conversation").glob("*.json"))


class TestSwapBetweenSpawnAndExecution:
    """The acceptance criterion from issue #144, in both directions.

    The gate Event guarantees the swap lands after the thread has started but
    before it writes -- the exact window the bug lived in.
    """

    def _run_swap_race(self, spawn_vault, swap_to_vault, write_record):
        """Spawn a bound writer, swap the global override while it is parked,
        then release it. Returns nothing; caller asserts on the vaults."""
        released = threading.Event()
        started = threading.Event()
        seen_by_thread = {}

        def _deferred_write():
            started.set()
            released.wait(timeout=10)
            seen_by_thread["vault"] = get_private_vault_path()
            write_record("synthetic record written by a deferred thread")

        set_vault_path_override(str(spawn_vault), "spawn")
        thread = spawn_vault_bound_thread(_deferred_write, vault=spawn_vault)

        assert started.wait(timeout=10), "thread never started"
        # The swap lands while the thread is parked, mid-flight.
        set_vault_path_override(str(swap_to_vault), "swapped")
        released.set()
        thread.join(timeout=10)
        assert not thread.is_alive(), "thread did not finish"

        return seen_by_thread["vault"]

    def test_write_lands_in_spawn_time_vault_when_swap_moves_away(
        self, tmp_path, write_record
    ):
        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")

        seen = self._run_swap_race(vault_a, vault_b, write_record)

        assert seen == vault_a.resolve()
        assert len(_records_in(vault_a)) == 1
        assert _records_in(vault_b) == []

    def test_write_lands_in_spawn_time_vault_in_the_other_direction(
        self, tmp_path, write_record
    ):
        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")

        seen = self._run_swap_race(vault_b, vault_a, write_record)

        assert seen == vault_b.resolve()
        assert len(_records_in(vault_b)) == 1
        assert _records_in(vault_a) == []

    def test_unbound_thread_follows_the_swap(self, tmp_path, write_record):
        """Control: without the binding the write follows the swap. This is
        the pre-fix behaviour, and proves the tests above are not vacuous."""
        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")

        released = threading.Event()
        started = threading.Event()

        def _deferred_write():
            started.set()
            released.wait(timeout=10)
            write_record("synthetic record written by an unbound thread")

        set_vault_path_override(str(vault_a), "a")
        thread = threading.Thread(target=_deferred_write, daemon=True)
        thread.start()

        assert started.wait(timeout=10)
        set_vault_path_override(str(vault_b), "b")
        released.set()
        thread.join(timeout=10)

        assert _records_in(vault_a) == []
        assert len(_records_in(vault_b)) == 1


class TestBindingPrecedence:
    """The binding is consulted before the swap override."""

    def test_binding_wins_over_a_later_override(self, tmp_path):
        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")

        set_vault_path_override(str(vault_a), "a")
        with vault_binding(vault_a):
            set_vault_path_override(str(vault_b), "b")
            assert get_private_vault_path() == vault_a.resolve()

        # Outside the block the override is visible again.
        assert get_private_vault_path() == vault_b.resolve()

    def test_none_binding_is_a_no_op(self, tmp_path):
        vault_a = make_vault(tmp_path / "vault_a")
        set_vault_path_override(str(vault_a), "a")

        with vault_binding(None):
            assert get_private_vault_path() == vault_a.resolve()
            assert get_bound_vault() is None

    def test_binding_resets_on_exception(self, tmp_path):
        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")
        set_vault_path_override(str(vault_a), "a")

        with pytest.raises(RuntimeError):
            with vault_binding(vault_b):
                raise RuntimeError("synthetic failure inside a bound block")

        assert get_bound_vault() is None
        assert get_private_vault_path() == vault_a.resolve()

    def test_nested_bindings_restore_the_outer_one(self, tmp_path):
        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")

        with vault_binding(vault_a):
            with vault_binding(vault_b):
                assert get_private_vault_path() == vault_b.resolve()
            assert get_private_vault_path() == vault_a.resolve()
        assert get_bound_vault() is None


class TestBindingIsolation:
    """A binding belongs to one thread and does not leak out of it."""

    def test_binding_in_a_thread_is_invisible_to_the_caller(self, tmp_path):
        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")
        set_vault_path_override(str(vault_a), "a")

        thread = spawn_vault_bound_thread(lambda: None, vault=vault_b)
        thread.join(timeout=10)

        assert get_bound_vault() is None
        assert get_private_vault_path() == vault_a.resolve()

    def test_two_bound_threads_do_not_see_each_other(self, tmp_path):
        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")

        seen = {}
        both_started = threading.Barrier(2, timeout=10)

        def _record(key):
            def _run():
                # Both threads are inside their bindings at the same time.
                both_started.wait()
                seen[key] = get_private_vault_path()
            return _run

        t_a = spawn_vault_bound_thread(_record("a"), vault=vault_a)
        t_b = spawn_vault_bound_thread(_record("b"), vault=vault_b)
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        assert seen["a"] == vault_a.resolve()
        assert seen["b"] == vault_b.resolve()


class TestSpawnHelperCapture:
    """What spawn_vault_bound_thread binds when vault is not passed."""

    def test_explicit_vault_beats_ambient_binding_and_override(self, tmp_path):
        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")
        vault_c = make_vault(tmp_path / "vault_c")

        seen = {}
        set_vault_path_override(str(vault_a), "a")

        with vault_binding(vault_b):
            thread = spawn_vault_bound_thread(
                lambda: seen.setdefault("vault", get_private_vault_path()),
                vault=vault_c,
            )
            thread.join(timeout=10)

        assert seen["vault"] == vault_c.resolve()

    def test_omitted_vault_captures_the_effective_vault_at_spawn(self, tmp_path):
        vault_a = make_vault(tmp_path / "vault_a")
        vault_b = make_vault(tmp_path / "vault_b")

        seen = {}
        released = threading.Event()
        started = threading.Event()

        def _run():
            started.set()
            released.wait(timeout=10)
            seen["vault"] = get_private_vault_path()

        set_vault_path_override(str(vault_a), "a")
        thread = spawn_vault_bound_thread(_run)

        assert started.wait(timeout=10)
        set_vault_path_override(str(vault_b), "b")
        released.set()
        thread.join(timeout=10)

        assert seen["vault"] == vault_a.resolve()

    def test_args_are_passed_through(self, tmp_path):
        vault = make_vault(tmp_path / "vault")
        seen = {}

        def _run(first, second):
            seen["args"] = (first, second)

        thread = spawn_vault_bound_thread(_run, args=("one", 2), vault=vault)
        thread.join(timeout=10)

        assert seen["args"] == ("one", 2)


class TestDeferredSitesUseTheHelper:
    """Grep guard: the deferred write paths cannot regress to raw spawns.

    Matches the convention in tests/test_eval_helpers.py. A raw
    threading.Thread in these files resolves the vault at run time again,
    which is the whole of issue #144.
    """

    def _source(self, *parts):
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[1]
        return repo_root.joinpath(*parts).read_text(encoding="utf-8")

    def test_openai_adapter_has_no_raw_thread_spawns(self):
        source = self._source("src", "api", "openai_adapter.py")
        assert "threading.Thread(" not in source
        assert "spawn_vault_bound_thread(" in source

    def test_llm_adapter_has_no_raw_thread_spawns(self):
        source = self._source("src", "llm", "adapter.py")
        assert "threading.Thread(" not in source
        assert "spawn_vault_bound_thread(" in source

    def test_delete_conversation_uses_the_helper(self):
        source = self._source("src", "api", "main.py")
        assert "spawn_vault_bound_thread(" in source


class TestBindingSurvivesModuleReload:
    """Regression: several fixtures reload src.core.config, which rebinds the
    module global to a fresh ContextVar. A token taken before the reload used
    to fail its reset with "Token was created by a different ContextVar",
    surfacing as PytestUnhandledThreadExceptionWarning and leaving the binding
    set. vault_binding holds the ContextVar object it set, so the reset always
    matches."""

    def test_exit_after_reload_does_not_raise(self, tmp_path):
        import importlib

        import src.core.config as cfg

        vault = make_vault(tmp_path / "vault")
        try:
            with cfg.vault_binding(vault):
                importlib.reload(cfg)
        finally:
            importlib.reload(cfg)

    def test_bound_thread_survives_a_reload_mid_flight(self, tmp_path):
        import importlib

        import src.core.config as cfg

        vault = make_vault(tmp_path / "vault")
        started = threading.Event()
        released = threading.Event()
        errors = []

        def _run():
            started.set()
            released.wait(timeout=10)

        try:
            thread = cfg.spawn_vault_bound_thread(_run, vault=vault)
            assert started.wait(timeout=10)
            importlib.reload(cfg)
            released.set()
            thread.join(timeout=10)
            assert not thread.is_alive()
        finally:
            importlib.reload(cfg)

        assert errors == []
