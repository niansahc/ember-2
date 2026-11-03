"""
tests/test_ingest_concurrency.py

Ingests overlap: upload and JSON import run on worker threads, as the sync
ingest routes always did. An overlapping ingest must not cost another ingest
its index entries, and two uploads with one filename must each parse their own
bytes.

Overlap is forced, not hoped for: each ingest's first embed waits on a barrier
until the other has started, so both are inside the window at once.

Positive controls replay the defects: a stale snapshot saved at the end of an
ingest loses entries, and parsing from the shared upload path mixes bytes.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.api.routes import ingest as ingest_route
from src.ingest import writers
from src.retrieval.vector_index import VectorIndex, clear_index_cache
from tests.conftest import _make_vault_tree, run_in_threads

FILLER = "Synthetic harbor notes about tides, ropes and lanterns for the winter. " * 12


def _text(word: str) -> str:
    return f"{word} {FILLER}"


@pytest.fixture
def vault(tmp_path):
    from src.core import config

    root = tmp_path / "vault"
    _make_vault_tree(root)
    config.set_vault_path_override(str(root), "test")
    return root


@pytest.fixture
def client(keyless_client, vault):
    with keyless_client as c:
        yield c


@pytest.fixture
def overlapping_embeds(monkeypatch):
    """Hold each ingest at its first embed until the other reaches its own.
    The chunk writer then finishes last, so a stale save would land on top of
    whatever the other ingest wrote."""
    barrier = threading.Barrier(2, timeout=20)
    entered = threading.local()

    def wait_once():
        if not getattr(entered, "done", False):
            entered.done = True
            barrier.wait()

    def chunk_embed(_text):
        wait_once()
        time.sleep(0.2)
        return [0.1] * 768

    def memory_embed(_text):
        wait_once()
        return [0.1] * 768

    monkeypatch.setattr(writers, "embed_text", chunk_embed)
    monkeypatch.setattr("src.memory.write_memory.embed_text", memory_embed)


def _upload(word):
    def send(client, vault):
        return client.post(
            "/ingest/upload",
            files={"file": (f"{word}.txt", _text(word).encode(), "text/plain")},
        )
    return send


def _import_json(word):
    def send(client, vault):
        return client.post("/ingest/json", json=[{"text": _text(word)}])
    return send


def _ingest_file(word):
    def send(client, vault):
        (vault / "imports" / f"{word}.txt").write_text(_text(word), encoding="utf-8")
        return client.post("/ingest/file", params={"file_path": f"{word}.txt"})
    return send


SCENARIOS = {
    "upload+upload": (_upload("alpha"), _upload("beta")),
    "upload+json": (_upload("gamma"), _import_json("kappa")),
    "file+upload": (_ingest_file("delta"), _upload("epsilon")),
    "file+file": (_ingest_file("zeta"), _ingest_file("theta")),
}
WORDS = {
    "upload+upload": {"alpha", "beta"},
    "upload+json": {"gamma", "kappa"},
    "file+upload": {"delta", "epsilon"},
    "file+file": {"zeta", "theta"},
}


def _run(scenario, client, vault):
    first, second = SCENARIOS[scenario]
    senders = [first, second]
    results, errors = run_in_threads(lambda i: senders[i](client, vault), 2)
    assert errors == []
    assert [r.status_code for r in results] == [200, 200]


def _indexed_words(vault: Path, scenario: str) -> set[str]:
    index = vault / "embeddings" / "ingested_index.json"
    clear_index_cache()
    entries = json.loads(index.read_text(encoding="utf-8"))
    return {w for w in WORDS[scenario] if any(w in e["text"] for e in entries)}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_overlapping_ingests_keep_every_index_entry(
    scenario, client, vault, overlapping_embeds,
):
    _run(scenario, client, vault)
    assert _indexed_words(vault, scenario) == WORDS[scenario]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_control_a_stale_snapshot_loses_entries(
    scenario, client, vault, overlapping_embeds, monkeypatch,
):
    """The defect: an ingest saves what it read before embedding, plus its
    own entries. Index empty at the start, as in the scenarios above."""

    def save_stale_snapshot(index_path, entries):
        writers.vector_index.save_index(index_path, entries)

    monkeypatch.setattr(writers.vector_index, "merge_entries", save_stale_snapshot)
    _run(scenario, client, vault)
    assert len(_indexed_words(vault, scenario)) == 1


def _entry(path: str, text: str = "t") -> dict:
    return {"file_path": path, "embedding": [0.0], "text": text, "metadata": {}}


def test_merge_entries_updates_in_place_and_keeps_the_rest(tmp_path):
    index = VectorIndex()
    path = tmp_path / "index.json"
    index.save_index(path, [_entry("a", "old"), _entry("b"), {"text": "no file path"}])
    cached = index.load_index(path)

    index.merge_entries(path, [_entry("a", "new"), _entry("c")])

    merged = index.load_index(path)
    assert [e.get("file_path") for e in merged] == ["a", "b", None, "c"]
    assert merged[0]["text"] == "new"
    # The list readers already hold is not changed under them.
    assert cached[0]["text"] == "old"


def test_merge_entries_serializes_with_append_entry(tmp_path):
    index = VectorIndex()
    path = tmp_path / "index.json"

    def write(i):
        if i % 2:
            index.append_entry(path, _entry(f"appended{i}"))
        else:
            index.merge_entries(path, [_entry(f"merged{i}")])

    _, errors = run_in_threads(write, 8)
    assert errors == []
    assert len(index.load_index(path)) == 8


def _same_name_uploads(client, vault, monkeypatch, *, parse_path=None):
    """Two uploads of one filename, both written before either is read.
    Returns (what each loader read, the kept source bytes)."""
    if parse_path is not None:
        monkeypatch.setattr(ingest_route, "_parse_path", parse_path)
    monkeypatch.setattr(writers, "embed_text", lambda _t: [0.1] * 768)
    real_load = ingest_route.load_text_file
    barrier = threading.Barrier(2, timeout=20)
    seen: list[str] = []

    def load_after_both_wrote(path, *args, **kwargs):
        barrier.wait()
        docs = real_load(path, *args, **kwargs)
        seen.append(docs[0].content.split()[0])
        return docs

    monkeypatch.setattr(ingest_route, "load_text_file", load_after_both_wrote)
    bodies = [_text("first"), _text("second")]

    def post(i):
        return client.post(
            "/ingest/upload",
            files={"file": ("same.txt", bodies[i].encode(), "text/plain")},
        )

    results, errors = run_in_threads(post, 2)
    assert errors == []
    assert [r.status_code for r in results] == [200, 200]
    kept = (vault / "imports" / "uploads" / "same.txt").read_bytes()
    return sorted(seen), kept


def test_same_name_uploads_each_parse_their_own_bytes(client, vault, monkeypatch):
    seen, kept = _same_name_uploads(client, vault, monkeypatch)
    assert seen == ["first", "second"]
    assert kept in (_text("first").encode(), _text("second").encode())


def test_no_work_directory_is_left_behind(client, vault, monkeypatch):
    _same_name_uploads(client, vault, monkeypatch)
    assert [p.name for p in (vault / "imports" / "uploads").iterdir()] == ["same.txt"]


def test_control_a_failed_cleanup_leaves_the_directory_and_says_so(
    client, vault, monkeypatch, caplog,
):
    """The test above would also pass if no work directory were ever made.
    With removal failing, the directories stay and each is logged."""
    monkeypatch.setattr(ingest_route, "shutil", SimpleNamespace(rmtree=lambda *a, **k: None))
    with caplog.at_level(logging.WARNING, logger="ember.ingest"):
        _same_name_uploads(client, vault, monkeypatch)

    left = [p for p in (vault / "imports" / "uploads").iterdir() if p.is_dir()]
    assert len(left) == 2
    warned = [r.getMessage() for r in caplog.records
              if r.getMessage().startswith("[UPLOAD] Work directory left behind")]
    assert sorted(warned) == sorted(
        f"[UPLOAD] Work directory left behind: {p.name}" for p in left)


def _parse_copies(client, monkeypatch, name, body) -> int:
    """How many parse copies one upload wrote."""
    monkeypatch.setattr(writers, "embed_text", lambda _t: [0.1] * 768)
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.1] * 768)
    real = ingest_route._parse_path
    calls = []

    def counting(work_dir, saved_path):
        calls.append(saved_path.name)
        return real(work_dir, saved_path)

    monkeypatch.setattr(ingest_route, "_parse_path", counting)
    response = client.post("/ingest/upload", files={"file": (name, body, "application/octet-stream")})
    assert response.status_code == 200
    return len(calls)


def test_a_json_upload_writes_no_parse_copy(client, monkeypatch):
    body = json.dumps([{"text": _text("iota")}]).encode()
    assert _parse_copies(client, monkeypatch, "records.json", body) == 0


def test_control_a_text_upload_writes_one_parse_copy(client, monkeypatch):
    assert _parse_copies(client, monkeypatch, "notes.txt", _text("lambda").encode()) == 1


def test_control_parsing_the_shared_path_mixes_uploads(client, vault, monkeypatch):
    seen, _ = _same_name_uploads(
        client, vault, monkeypatch,
        parse_path=lambda work_dir, saved_path: saved_path,
    )
    assert len(set(seen)) == 1
