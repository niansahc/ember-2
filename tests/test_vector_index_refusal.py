"""
tests/test_vector_index_refusal.py

An index that cannot be read (oversized or corrupt) is skipped by readers.
A writer that loaded it the same way saw an empty list and saved only its own
entries over the whole file. Writers now refuse to save over an unreadable
index; it is derived, so scripts/rebuild_indexes.py rebuilds it.

Controls: a missing file and a valid file still take the write.
"""

from __future__ import annotations

import json
import logging

import pytest

from src.retrieval import vector_index as vi_mod
from src.retrieval.vector_index import VectorIndex

NEW = {"file_path": "new.json", "embedding": [0.1], "text": "synthetic new"}


@pytest.fixture(autouse=True)
def fresh_cache():
    vi_mod.clear_index_cache()
    yield
    vi_mod.clear_index_cache()


def _valid(path, n=3):
    entries = [{"file_path": f"old{i}.json", "embedding": [0.0], "text": f"old {i}"}
               for i in range(n)]
    path.write_text(json.dumps(entries), encoding="utf-8")
    return entries


def _oversized(path):
    """A valid file over a 0 MB limit."""
    _valid(path)
    index = VectorIndex()
    index.max_index_size_mb = 0
    return index


def _corrupt(path):
    path.write_text('[{"file_path": "old0.json", "embedd', encoding="utf-8")
    return VectorIndex()


@pytest.mark.parametrize("make", [_oversized, _corrupt], ids=["oversized", "corrupt"])
@pytest.mark.parametrize("write", ["merge", "append"])
def test_an_unreadable_index_is_not_overwritten(tmp_path, caplog, make, write):
    path = tmp_path / "ingested_index.json"
    index = make(path)
    before = path.read_bytes()

    with caplog.at_level(logging.WARNING, logger="ember.vector_index"):
        if write == "merge":
            saved = index.merge_entries(path, [NEW])
        else:
            saved = index.append_entry(path, NEW)

    assert saved is False
    assert path.read_bytes() == before
    assert any("Refusing to overwrite unreadable index" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("write", ["merge", "append"])
def test_control_a_missing_index_takes_the_write(tmp_path, write):
    path = tmp_path / "ingested_index.json"
    index = VectorIndex()
    saved = index.merge_entries(path, [NEW]) if write == "merge" else index.append_entry(path, NEW)

    assert saved is True
    assert json.loads(path.read_text(encoding="utf-8")) == [NEW]


@pytest.mark.parametrize("write", ["merge", "append"])
def test_control_a_valid_index_takes_the_write(tmp_path, write):
    path = tmp_path / "ingested_index.json"
    old = _valid(path)
    index = VectorIndex()
    saved = index.merge_entries(path, [NEW]) if write == "merge" else index.append_entry(path, NEW)

    assert saved is True
    assert json.loads(path.read_text(encoding="utf-8")) == old + [NEW]
