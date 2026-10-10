"""
tests/test_vector_index_concurrency.py

The JSON-fallback index path in write_memory used to run
load_index -> append -> save_index with no lock. Two writers that each
loaded their own copy of the index saved over each other, and every entry
but the last writer's was lost (ultrareview #280, item 4). On Windows the
same race also surfaces as a PermissionError: os.replace() onto an index
file that another thread is replacing or reading fails, and write_memory
raises after the canonical record is already on disk.

The race is made deterministic by slowing the index read and write inside
src.retrieval.vector_index and clearing the index cache first, so every
thread loads its own list while the others are still between load and
save. Without a lock held across the whole sequence, entries are lost (or
writes fail) on every run; with one, the threads serialize and neither
happens.

The positive control runs the old unlocked sequence through the same
harness and asserts the defect does occur, so the zero-loss assertion
cannot pass vacuously.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

import src.retrieval.vector_index as vector_index_mod
from src.core.config import get_private_vault_path
from src.memory.write_memory import write_memory
from src.retrieval.vector_index import VectorIndex, clear_index_cache

THREADS = 8
IO_DELAY_SECONDS = 0.05
# Not in SQLITE_MEMORY_TYPES, so write_memory indexes it into a JSON index.
JSON_INDEXED_TYPE = "decision"


@pytest.fixture(autouse=True)
def stub_embeddings(monkeypatch):
    """write_memory always embeds; no Ollama dependency for these tests."""
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 8)


@pytest.fixture
def slow_index_io(monkeypatch):
    """Widen the load/save window so concurrent writers overlap."""
    real_read = vector_index_mod.safe_read_json
    real_write = vector_index_mod.safe_write_json

    def slow_read(*args, **kwargs):
        data = real_read(*args, **kwargs)
        time.sleep(IO_DELAY_SECONDS)
        return data

    def slow_write(*args, **kwargs):
        time.sleep(IO_DELAY_SECONDS)
        return real_write(*args, **kwargs)

    monkeypatch.setattr(vector_index_mod, "safe_read_json", slow_read)
    monkeypatch.setattr(vector_index_mod, "safe_write_json", slow_write)


def _run_threads(fn, threads: int = THREADS) -> list[BaseException]:
    """Run fn(i) on `threads` threads started together; return their errors."""
    barrier = threading.Barrier(threads)
    errors: list[BaseException] = []
    errors_lock = threading.Lock()

    def worker(i: int):
        try:
            barrier.wait()
            fn(i)
        except BaseException as exc:  # collected and asserted by the caller
            with errors_lock:
                errors.append(exc)

    workers = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join(timeout=60)
    return errors


def _index_texts(index_path) -> set[str]:
    if not index_path.exists():
        return set()
    with index_path.open(encoding="utf-8") as f:
        return {entry.get("text") for entry in json.load(f)}


def test_concurrent_json_index_appends_lose_no_entries(slow_index_io):
    index_path = VectorIndex().get_index_path(get_private_vault_path(), JSON_INDEXED_TYPE)
    clear_index_cache(str(index_path))
    texts = [
        f"Synthetic decision record number {i} for the index concurrency test."
        for i in range(THREADS)
    ]

    def write_one(i: int) -> None:
        assert write_memory(text=texts[i], memory_type=JSON_INDEXED_TYPE, source="test")

    errors = _run_threads(write_one)

    assert not errors, errors
    missing = set(texts) - _index_texts(index_path)
    assert not missing, f"{len(missing)} of {THREADS} index entries lost"


def test_control_unlocked_load_append_save_loses_entries(slow_index_io, tmp_path):
    """The harness must be able to produce the defect: an unlocked
    read -> append -> write on the same slowed I/O loses entries or fails
    writes. It goes through the module's (slowed) read/write functions
    directly, not load_index/save_index, so it models the pre-fix sequence
    regardless of the locking those methods now do."""
    index_path = tmp_path / "control_index.json"
    index_path.write_text("[]", encoding="utf-8")
    texts = [f"control entry {i}" for i in range(THREADS)]

    def unlocked_append(i: int) -> None:
        data = vector_index_mod.safe_read_json(index_path, default=None) or []
        data.append({"text": texts[i]})
        vector_index_mod.safe_write_json(index_path, data)

    errors = _run_threads(unlocked_append)
    missing = set(texts) - _index_texts(index_path)

    assert errors or missing
