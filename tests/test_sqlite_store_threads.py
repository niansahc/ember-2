"""
tests/test_sqlite_store_threads.py

One SqliteVectorStore per database is shared by every caller in the process
(src/retrieval/store_cache.py), over one connection opened with
check_same_thread=False. Since chat turns run on worker threads, turns from
different sessions write through it at the same time, and two inserts at once
on one connection fail inside SQLite with InterfaceError ("bad parameter or
other API misuse"), or with IndexError or SystemError from the sqlite3
module. The store serializes every use of its connection.

Control: the same writes with the store's lock removed reproduce the error.
"""

from __future__ import annotations

import contextlib

import pytest

from src.retrieval.sqlite_vector_store import SqliteVectorStore
from tests.conftest import run_in_threads

PER_THREAD = 300


@pytest.fixture
def store(tmp_path):
    s = SqliteVectorStore(tmp_path / "memory.db")
    yield s
    s.close()


def _insert_from_two_threads(store) -> list[BaseException]:
    def inserts(i):
        failures = []
        for k in range(PER_THREAD):
            try:
                store.insert({
                    "id": f"sess_test_{i}_{k}", "text": f"synthetic record {i} {k}",
                    "embedding": [0.1] * 768, "memory_type": "conversation", "metadata": {},
                })
            except Exception as exc:  # noqa: BLE001 - collected and asserted
                failures.append(exc)
        return failures

    results, errors = run_in_threads(inserts, 2)
    assert errors == []
    return [exc for failures in results for exc in failures]


def test_concurrent_inserts_all_land(store):
    assert _insert_from_two_threads(store) == []
    assert store.count() == 2 * PER_THREAD


def test_control_without_the_lock_concurrent_inserts_fail(store):
    store._lock = contextlib.nullcontext()
    failures = _insert_from_two_threads(store)

    # Seen here: InterfaceError, plus IndexError and SystemError raised from
    # inside the sqlite3 module when one connection is used by two threads.
    assert failures, "no failure: the harness did not reach the race"


def test_inserts_and_searches_at_once_both_complete(store):
    def work(i):
        for k in range(100):
            if i == 0:
                store.insert({
                    "id": f"sess_test_m_{k}", "text": f"synthetic mixed {k}",
                    "embedding": [0.1] * 768, "memory_type": "conversation", "metadata": {},
                })
            else:
                store.search([0.1] * 768, limit=5)

    _, errors = run_in_threads(work, 2)
    assert errors == []
    assert store.count() == 100
