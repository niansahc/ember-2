"""
tests/test_ingest_index_routing.py

Issue #174 / B-RET-003: ingested chunks must land in the store retrieval
reads. The ingest writer used to save embeddings to ingested_index.json
while semantic_search reads `ingested` only from ingested.db, so every
uploaded or imported document was written to the vault and never
retrievable.

All content is synthetic. Embeddings come from a deterministic
bag-of-words hash so a query and the chunk that contains its words score
high against each other without a model.
"""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import _make_vault_tree

DIM = 64


def fake_embed(text: str) -> list[float]:
    """Deterministic bag-of-words embedding, unit length."""
    vector = [0.0] * DIM
    for word in text.lower().split():
        word = word.strip(".,:;!?\"'()")
        if not word:
            continue
        slot = int(hashlib.sha256(word.encode()).hexdigest(), 16) % DIM
        vector[slot] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


DOC_TEXT = (
    "The lighthouse keeper logs the tide tables every morning and checks "
    "the lamp lens for salt before the evening watch begins."
)
QUERY = "When does the lighthouse keeper check the lamp lens?"


@pytest.fixture
def vault(tmp_path: Path):
    """A throwaway vault, active for this test (restored by conftest)."""
    from src.core.config import set_vault_path_override

    root = tmp_path / "vault"
    _make_vault_tree(root)
    set_vault_path_override(str(root), "test")
    return root


@pytest.fixture
def client(vault: Path):
    with patch("src.api.main.get_ember_api_key", return_value=None), \
         patch("src.ingest.writers.embed_text", side_effect=fake_embed):
        from src.api.main import app
        yield TestClient(app)


def search(query: str, memory_type: str | None = None, limit: int = 5) -> list[dict]:
    from src.retrieval.semantic_search import semantic_search

    return semantic_search(
        query, limit=limit, memory_type=memory_type, query_embedding=fake_embed(query),
    )


def upload_text(client: TestClient, name: str, text: str):
    response = client.post(
        "/ingest/upload", files={"file": (name, text.encode("utf-8"), "text/plain")},
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestUploadedDocumentIsRetrievable:
    def test_uploaded_document_chunk_is_returned_by_semantic_search(self, client):
        upload_text(client, "keeper_notes.txt", DOC_TEXT)

        results = search(QUERY, memory_type="ingested")

        assert results, "uploaded chunk must be retrievable"
        top = results[0]
        assert top["content"] == DOC_TEXT
        assert top["memory_type"] == "ingested"
        assert top["path"] and top["path"].endswith("keeper_notes_chunk_0.json")

    def test_first_ingest_into_a_fresh_vault_creates_ingested_db(self, client, vault):
        assert not (vault / "embeddings" / "ingested.db").exists()
        upload_text(client, "keeper_notes.txt", DOC_TEXT)
        assert (vault / "embeddings" / "ingested.db").exists()

    def test_entry_only_in_the_json_index_is_not_retrievable(self, vault):
        """Pins the reader contract #174 broke against: ingested_index.json
        is not read. Positive control: the upload test above, where the same
        content written through the writer is returned."""
        from src.core.jsonio import safe_write_json

        safe_write_json(vault / "embeddings" / "ingested_index.json", [{
            "file_path": str(vault / "memory" / "ingested" / "legacy_chunk_0.json"),
            "embedding": fake_embed(DOC_TEXT),
            "text": DOC_TEXT,
            "metadata": {"source": "file"},
        }])

        assert search(QUERY) == []


def chatgpt_chunks(messages: list[str], roles: list[str]):
    from src.ingest.chunking import chunk_document
    from src.ingest.models import NormalizedDocument

    return chunk_document(NormalizedDocument(
        source="chatgpt",
        doc_id="conversations-000_0",
        title="Synthetic chat",
        created_at="2026-02-01T10-00-00",
        content="\n\n".join(messages),
        metadata={"type": "chatgpt_export", "file": "conversations-000.json",
                  "messages": messages, "roles": roles},
    ))


USER_TURN = "User: I repainted the garden shed green over the long weekend."
ASSISTANT_TURN = "Assistant: Green suits a garden shed and hides the moss well."


def write(chunks, vault: Path) -> None:
    from src.ingest.writers import write_chunks_to_vault

    with patch("src.ingest.writers.embed_text", side_effect=fake_embed):
        write_chunks_to_vault(chunks, vault)


class TestChatgptImportRouting:
    """ADR-015: an imported ChatGPT export is conversation. ADR-033: its
    assistant turns are written to disk but not indexed."""

    def test_user_turn_is_indexed_as_conversation(self, vault):
        write(chatgpt_chunks([USER_TURN], ["user"]), vault)

        results = search("repainted the garden shed green", memory_type="conversation")

        assert [r["content"] for r in results] == [USER_TURN]
        assert results[0]["authorship"] == "first_person"
        assert search("repainted the garden shed green", memory_type="ingested") == []

    def test_assistant_turn_is_not_indexed_in_either_store(self, vault):
        write(chatgpt_chunks([USER_TURN, ASSISTANT_TURN], ["user", "assistant"]), vault)

        contents = [r["content"] for r in search("garden shed green moss", limit=10)]

        # Positive control: the user turn from the same export is indexed.
        assert USER_TURN in contents
        assert ASSISTANT_TURN not in contents

    def test_indexed_metadata_omits_the_whole_conversation_lists(self, vault):
        """Every chunk's metadata inherits the document's full `messages` and
        `roles` lists. They stay in the canonical file; copying them onto every
        index row is what grew the old JSON index past its load guard."""
        write(chatgpt_chunks([USER_TURN], ["user"]), vault)

        metadata = search("repainted the garden shed green", memory_type="conversation")[0]["metadata"]

        assert "messages" not in metadata and "roles" not in metadata
        assert metadata["role"] == "user"
        assert metadata["file_path"].endswith("conversations-000_0_chunk_0.json")


def row_ids(vault: Path, db_name: str) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(str(vault / "embeddings" / db_name))
    try:
        return sorted(r[0] for r in conn.execute("SELECT id FROM vectors"))
    finally:
        conn.close()


class TestReingest:
    def test_unchanged_document_is_not_indexed_or_embedded_again(self, vault):
        from src.ingest.writers import write_chunks_to_vault

        chunks = chatgpt_chunks([USER_TURN], ["user"])
        write(chunks, vault)
        with patch("src.ingest.writers.embed_text", side_effect=fake_embed) as second:
            write_chunks_to_vault(chunks, vault)

        assert row_ids(vault, "memory.db") == ["conversations-000_0_chunk_0"]
        assert second.call_count == 0

    def test_changed_document_replaces_its_row(self, client, vault):
        upload_text(client, "keeper_notes.txt", DOC_TEXT)
        revised = DOC_TEXT.replace("evening watch", "midnight watch")
        upload_text(client, "keeper_notes.txt", revised)

        contents = [r["content"] for r in search(QUERY, memory_type="ingested", limit=10)]

        assert contents == [revised]
        assert row_ids(vault, "ingested.db") == ["keeper_notes_chunk_0"]

    def test_text_already_indexed_under_another_id_is_not_duplicated(self, vault):
        """The migrated corpus is keyed by absolute file path, not chunk_id.
        Re-importing a cumulative export must not add those turns twice."""
        from src.retrieval.store_cache import get_store

        store = get_store(vault / "embeddings" / "memory.db")
        store.insert({"id": "legacy-path-id", "text": USER_TURN,
                      "embedding": fake_embed(USER_TURN), "memory_type": "conversation"})
        other = "User: I also cleared the gutters before the autumn storms arrived."

        write(chatgpt_chunks([USER_TURN, other], ["user", "user"]), vault)

        # Positive control: the turn with new text is added.
        assert row_ids(vault, "memory.db") == ["conversations-000_0_chunk_1", "legacy-path-id"]


def doc_chunks(doc_id: str, texts: list[str], source: str = "file"):
    from src.ingest.models import ChunkedDocument

    return [
        ChunkedDocument(source=source, doc_id=doc_id, chunk_id=f"{doc_id}_chunk_{i}",
                        title=doc_id, created_at=None, content=text,
                        metadata={"chunk_index": i})
        for i, text in enumerate(texts)
    ]


def rows(vault: Path, db_name: str = "ingested.db") -> dict[str, str]:
    import sqlite3

    conn = sqlite3.connect(str(vault / "embeddings" / db_name))
    try:
        return dict(conn.execute("SELECT id, text FROM vectors").fetchall())
    finally:
        conn.close()


ALPHA = "Alpha paragraph: the ferry timetable changes on the first Monday of spring."
BETA = "Beta paragraph: the lighthouse lamp is serviced every second Thursday."
GAMMA = "Gamma paragraph: the harbour crane was repainted yellow last autumn."


class TestReingestWithShiftedText:
    """Lean review of #315, finding 1. Re-ingesting an edited document whose
    text moves to another chunk id: the writer skipped text whose only row it
    had just deleted, and kept the superseded row. Red on 4dbbe36 with rows
    {c0: gamma, c1: beta}."""

    def test_every_chunk_of_the_new_version_is_indexed_and_nothing_else(self, vault):
        write(doc_chunks("notes", [ALPHA, BETA]), vault)
        write(doc_chunks("notes", [GAMMA, ALPHA]), vault)

        assert rows(vault) == {"notes_chunk_0": GAMMA, "notes_chunk_1": ALPHA}

    def test_stale_row_goes_when_its_new_text_is_indexed_under_another_id(self, vault):
        write(doc_chunks("other", [GAMMA]), vault)
        write(doc_chunks("notes", [ALPHA]), vault)
        write(doc_chunks("notes", [GAMMA]), vault)

        # notes_chunk_0's file now holds gamma, already indexed under
        # other_chunk_0: the row with alpha has no file behind it any more.
        assert rows(vault) == {"other_chunk_0": GAMMA}

    def test_control_new_unique_text_under_the_same_id_is_inserted(self, vault):
        write(doc_chunks("notes", [ALPHA]), vault)
        write(doc_chunks("notes", [BETA]), vault)

        assert rows(vault) == {"notes_chunk_0": BETA}


def chunk_file_text(vault: Path, chunk_id: str) -> str:
    import json

    path = vault / "memory" / "ingested" / f"{chunk_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))["content"]


class TestOverlappingIngestsOfOneDocument:
    """Lean review of #315, finding 3. Ingest A writes its files, ingest B
    writes and indexes the next version, then A indexes: without one lock
    across write-then-index the file holds B and the row holds A."""

    def _race(self, vault: Path) -> None:
        import threading

        from src.ingest.writers import write_chunks_to_vault

        a_wrote, b_done = threading.Event(), threading.Event()

        def embed(text):
            if threading.current_thread().name == "ingest-a" and not a_wrote.is_set():
                a_wrote.set()
                b_done.wait(timeout=1.0)  # with the lock, B cannot finish meanwhile
            return fake_embed(text)

        def ingest_b():
            write_chunks_to_vault(doc_chunks("notes", [BETA]), vault)
            b_done.set()

        with patch("src.ingest.writers.embed_text", side_effect=embed):
            a = threading.Thread(name="ingest-a", target=write_chunks_to_vault,
                                 args=(doc_chunks("notes", [ALPHA]), vault))
            a.start()
            assert a_wrote.wait(timeout=5)
            b = threading.Thread(name="ingest-b", target=ingest_b)
            b.start()
            a.join(timeout=10)
            b.join(timeout=10)

    def test_file_and_row_end_on_the_same_version(self, vault):
        self._race(vault)

        assert rows(vault)["notes_chunk_0"] == chunk_file_text(vault, "notes_chunk_0")

    def test_control_without_the_document_lock_they_disagree(self, vault, monkeypatch):
        import threading

        monkeypatch.setattr("src.ingest.writers._document_lock", lambda *a: threading.Lock())

        self._race(vault)

        assert chunk_file_text(vault, "notes_chunk_0") == BETA
        assert rows(vault)["notes_chunk_0"] == ALPHA


class TestConcurrentIdenticalText:
    """Lean review of #315, finding 4. Two documents with the same text, both
    past the pre-embedding check before either writes: the check under the
    store lock lets only one row in."""

    def test_identical_text_from_two_racing_documents_is_indexed_once(self, vault, monkeypatch):
        import threading

        from src.ingest.writers import write_chunks_to_vault

        # Each ingest takes its own lock: this race is about the store check.
        monkeypatch.setattr("src.ingest.writers._document_lock", lambda *a: threading.Lock())
        barrier = threading.Barrier(2, timeout=5)
        embedded = []

        def embed(text):
            barrier.wait()  # both threads have passed the pre-embedding check
            embedded.append(text)
            return fake_embed(text)

        with patch("src.ingest.writers.embed_text", side_effect=embed):
            threads = [threading.Thread(target=write_chunks_to_vault,
                                        args=(doc_chunks(name, [ALPHA]), vault))
                       for name in ("d1", "d2")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)

        # Control: both really raced (both embedded), and one row went in.
        assert len(embedded) == 2
        assert list(rows(vault).values()) == [ALPHA]


class TestLogCarriesNoIds:
    def test_no_log_record_names_a_chunk(self, vault, caplog):
        """Lean review of #315, finding 7: a gated chunk's ids were logged.
        Control: the summary, counting that chunk, is captured."""
        chunks = chatgpt_chunks([USER_TURN, ASSISTANT_TURN], ["user", "assistant"])
        with caplog.at_level("DEBUG", logger="ember.ingest.writers"):
            write(chunks, vault)

        messages = [r.getMessage() for r in caplog.records]
        assert not any(c.chunk_id in m or c.doc_id in m for c in chunks for m in messages)
        assert any("1 held out by ADR-033" in m for m in messages)

    def test_writer_logs_where_each_chunk_was_indexed(self, vault, caplog):
        chunks = chatgpt_chunks([USER_TURN, ASSISTANT_TURN], ["user", "assistant"])
        with caplog.at_level("INFO", logger="ember.ingest.writers"):
            write(chunks, vault)

        assert any(
            "indexed 0 chunk(s) into ingested.db, 1 into memory.db; "
            "skipped 0 already indexed, 1 held out by ADR-033" in r.message
            for r in caplog.records
        )


class TestJsonImportOfIngestedType:
    def test_json_record_typed_ingested_is_retrievable(self, client):
        """/ingest/json goes through write_memory, not the chunk writer, and
        its `ingested` branch wrote the same unread JSON index."""
        with patch("src.memory.write_memory.embed_text", side_effect=fake_embed):
            response = client.post("/ingest/json", json=[{"text": DOC_TEXT, "type": "ingested"}])
        assert response.status_code == 200 and response.json()["imported"] == 1

        assert [r["content"] for r in search(QUERY, memory_type="ingested")] == [DOC_TEXT]


NATIVE_RECORDS = {
    "conversation": "User: The bakery on the corner now opens at six in the morning.",
    "profile": "The user works as a cartographer and draws coastal survey maps.",
    "reflection": "Across the week the user returned twice to the topic of tide charts.",
    "journal": "Walked the harbour wall at dusk and counted eleven fishing boats.",
}


class TestOtherCategoriesUnchanged:
    """Ingesting a document adds candidates; it must not change what any
    other memory type returns or how it orders."""

    def _seed_memory_db(self, vault: Path) -> None:
        from src.retrieval.store_cache import get_store

        store = get_store(vault / "embeddings" / "memory.db")
        for i, (memory_type, text) in enumerate(NATIVE_RECORDS.items()):
            for j in range(3):
                body = f"{text} Entry {j} about the lamp lens and tide tables."
                store.insert({"id": f"native-{i}-{j}", "text": body,
                              "embedding": fake_embed(body), "memory_type": memory_type})

    def _snapshot(self, memory_type):
        return [(r["id"], round(r["score"], 9)) for r in search(QUERY, memory_type=memory_type, limit=10)]

    def test_per_type_results_identical_before_and_after_an_upload(self, client, vault):
        self._seed_memory_db(vault)
        before = {t: self._snapshot(t) for t in NATIVE_RECORDS}
        all_before = self._snapshot(None)
        assert all(before.values()), "fixture must return results for every type"

        upload_text(client, "keeper_notes.txt", DOC_TEXT)

        assert {t: self._snapshot(t) for t in NATIVE_RECORDS} == before
        # Positive control: the unfiltered search does change, so this
        # fixture can see an upload when there is one to see.
        assert self._snapshot(None) != all_before
