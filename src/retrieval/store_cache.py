"""
src/retrieval/store_cache.py

Process-wide cache of SqliteVectorStore handles, keyed on resolved db path.

Ember can change its active vault at runtime (POST /v1/developer/vault/swap
sets a memory-only override in src.core.config). The stores that back
retrieval and memory writes used to be plain module-level singletons: one
variable per store, populated on first use and never revisited. After a
swap those singletons kept serving the previous vault, so reads returned
the old vault's records and writes landed in the old vault's memory.db,
silently.

Keying the cache on the resolved db path removes the failure class rather
than papering over it. A cache entry names exactly one file, accessors
resolve the active vault before asking for one, and there is no variable
that can hold "the store" independently of which vault it belongs to. A
stale store is unrepresentable, so no reset step is needed on swap.

Nothing is ever evicted or closed. A connection may be in use by a request
thread at any moment, and closing it underneath that thread would turn a
vault swap into a crash in unrelated work. Entries for a vault no longer
in use simply stop being reachable; the handles cost one file descriptor
each and are released when the process exits.
"""

from __future__ import annotations

import threading
from pathlib import Path

from src.retrieval.sqlite_vector_store import SqliteVectorStore


# Resolved db path (as a string) -> store. Guarded by _lock: FastAPI serves
# requests from a thread pool, and two threads resolving the same db path
# concurrently must not open two connections to it.
_stores: dict[str, SqliteVectorStore] = {}
_lock = threading.Lock()


def get_store(db_path: Path) -> SqliteVectorStore:
    """Return the store for db_path, opening it on first use.

    The returned store's own db_path always matches the requested one, so
    a caller that resolved the active vault correctly cannot be handed
    another vault's store.
    """
    key = str(Path(db_path).resolve())
    with _lock:
        store = _stores.get(key)
        if store is None:
            store = SqliteVectorStore(Path(key))
            _stores[key] = store
        return store


def cached_store_paths() -> list[str]:
    """Return the resolved db paths currently held, for tests and diagnostics."""
    with _lock:
        return sorted(_stores)


def clear_store_cache() -> None:
    """Drop all cached store references. Test teardown only.

    Connections are deliberately not closed, for the reason given in the
    module docstring. Production code has no reason to call this: the
    cache is self-correcting across vault swaps.
    """
    with _lock:
        _stores.clear()
