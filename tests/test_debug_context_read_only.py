"""
tests/test_debug_context_read_only.py

Coverage for the read_only path on ContextService.build_context (issue #206).

The assertion that matters is paired. Proving that /debug-context leaves the
database untouched is worthless on its own: a build that delivers no records
writes nothing either way, so the read-only test would pass vacuously on an
empty vault and on a broken retriever alike. Every read-only test here has a
writing counterpart over the same seeded corpus, and the writing one asserts
the digest DOES change. If seeding or retrieval ever stops working, the
writing test fails first and the pair stops being a false reassurance.

Digests are taken over the mutable columns rather than the file bytes:
last_retrieved_at, frequency_score, tier, heat_score and retrieval_count are
what a delivery changes, and SQLite is free to touch file bytes for its own
bookkeeping without any logical write having happened.
"""

import hashlib
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.context.service import ContextService
from src.core.config import get_private_vault_path
from src.memory.write_memory import write_memory

EMBED_DIM = 768
QUERY = "what have i been reading about lately"

client = TestClient(app)


def _memory_db() -> Path:
    return get_private_vault_path() / "embeddings" / "memory.db"


def _stats_digest() -> str:
    """Digest of every column a delivery is capable of changing."""
    path = _memory_db()
    if not path.exists():
        return "no-database"
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT id, last_retrieved_at, frequency_score, tier, heat_score, "
            "retrieval_count FROM vectors ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


def _file_digest() -> str:
    path = _memory_db()
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "no-database"


@pytest.fixture
def seeded_vault():
    """A handful of conversation records the retriever can actually deliver.

    Embeddings are stubbed to a constant vector, so every record matches the
    query at cosine 1.0 and the delivery window fills. That is deliberate:
    these tests are about whether a write happens, not about ranking.
    """
    vector = [0.1] * EMBED_DIM
    with patch("src.memory.write_memory.embed_text", return_value=vector):
        for i in range(6):
            write_memory(
                text=(
                    f"a substantive conversation turn about topic {i} with enough "
                    "length to clear the low-value floor and the short-content filter"
                ),
                memory_type="conversation",
                source="chat",
                metadata={"role": "user", "content_kind": "user_content"},
            )
    yield vector


@pytest.fixture
def stub_query_embedding(seeded_vault):
    with patch("src.retrieval.semantic_search.embed_text", return_value=seeded_vault):
        yield


@pytest.fixture
def open_api_auth():
    """Disable API-key auth for the endpoint tests.

    The middleware treats an unset key as open access. Patching the resolver
    keeps a real key out of the test rather than reading one from the keyring
    to hand straight back to the thing that issued it.
    """
    with patch("src.api.main.get_ember_api_key", return_value=""):
        yield


# ---------------------------------------------------------------------------
# The pair: the writing path must move the digest, the read-only path must not
# ---------------------------------------------------------------------------

def test_default_build_context_writes_retrieval_stats(stub_query_embedding):
    # Non-vacuity guard for every read-only assertion below. If this fails,
    # nothing is being delivered and the read-only tests prove nothing.
    service = ContextService()
    before = _stats_digest()

    packet = service.build_context(QUERY)

    assert packet.memory_items, "nothing delivered; the read-only tests would be vacuous"
    assert _stats_digest() != before


def test_read_only_build_context_does_not_write(stub_query_embedding):
    service = ContextService()
    service.build_context(QUERY)          # settle any first-delivery write
    before = _stats_digest()

    packet = service.build_context(QUERY, read_only=True)

    assert packet.memory_items
    assert _stats_digest() == before


def test_debug_context_endpoint_leaves_the_database_unchanged(stub_query_embedding, open_api_auth):
    # The endpoint-level version of the same assertion: this is the call that
    # #206 is about.
    client.get("/debug-context", params={"message": QUERY})
    before_stats = _stats_digest()
    before_bytes = _file_digest()

    response = client.get("/debug-context", params={"message": QUERY})

    assert response.status_code == 200
    assert _stats_digest() == before_stats
    assert _file_digest() == before_bytes


def test_debug_context_still_returns_a_populated_packet(stub_query_embedding, open_api_auth):
    response = client.get("/debug-context", params={"message": QUERY})

    assert response.status_code == 200
    assert response.json().get("memory_items")


# ---------------------------------------------------------------------------
# read_only gates the write and nothing else
# ---------------------------------------------------------------------------

def test_read_only_delivers_the_same_records_as_the_writing_path(stub_query_embedding):
    service = ContextService()
    writing = service.build_context(QUERY)
    read_only = service.build_context(QUERY, read_only=True)

    assert [i.store_id for i in read_only.memory_items] == [
        i.store_id for i in writing.memory_items
    ]


def test_read_only_skips_the_stats_call_entirely(stub_query_embedding):
    service = ContextService()
    with patch.object(service, "_update_retrieval_stats") as stats:
        service.build_context(QUERY, read_only=True)
    stats.assert_not_called()


def test_default_calls_the_stats_writer(stub_query_embedding):
    service = ContextService()
    with patch.object(service, "_update_retrieval_stats") as stats:
        service.build_context(QUERY)
    stats.assert_called_once()


def test_read_only_defaults_to_false(stub_query_embedding):
    # The chat path must keep writing. ADR-015's activation model has no other
    # upward input, so a default flip here would disable tiering's only
    # recovery path while looking like a bug fix.
    service = ContextService()
    with patch.object(service, "_update_retrieval_stats") as stats:
        service.build_context(QUERY)
    stats.assert_called_once()


# ---------------------------------------------------------------------------
# The endpoint is wired to it
# ---------------------------------------------------------------------------

def test_debug_context_endpoint_requests_read_only(open_api_auth):
    from src.api import main as api_main

    with patch.object(api_main.context_service, "build_context") as build:
        build.return_value = api_main.context_service.formatter.format(
            user_message=QUERY,
            memory_items=[],
            reflection_items=[],
            state_items=[],
            task_items=[],
            web_items=[],
            image_data=[],
        )
        client.get("/debug-context", params={"message": QUERY})

    assert build.call_args.kwargs.get("read_only") is True
