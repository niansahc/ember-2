"""tests/test_chatgpt_role_separation.py — ADR-033 role separation.

Verifies that assistant-role chunks from a ChatGPT import are written to
flat storage but skipped from embedding and the vector index. User-role
chunks remain fully indexed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.ingest.chunking import chunk_document
from src.ingest.models import ChunkedDocument, NormalizedDocument
from src.ingest.writers import write_chunks_to_vault


def _make_chatgpt_doc(messages, roles):
    return NormalizedDocument(
        source="chatgpt",
        doc_id="doc_test_0",
        title="Test Chat",
        created_at="2026-04-20",
        content="\n\n".join(messages),
        metadata={
            "type": "chatgpt_export",
            "file": "test.json",
            "messages": messages,
            "roles": roles,
        },
    )


def _read_index(vault_path: Path) -> list:
    index_file = vault_path / "embeddings" / "ingested_index.json"
    if not index_file.exists():
        return []
    with open(index_file, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_flat_chunks(vault_path: Path) -> list[dict]:
    ingested_dir = vault_path / "memory" / "ingested"
    records = []
    for p in sorted(ingested_dir.glob("*.json")):
        with open(p, "r", encoding="utf-8") as f:
            records.append(json.load(f))
    return records


class TestChatgptRoleSeparation:
    """ADR-033: assistant-role chunks must not enter the vector index."""

    def test_user_chunks_tagged_for_retrieval(self, tmp_path):
        doc = _make_chatgpt_doc(
            messages=["User: What projects am I working on right now?"],
            roles=["user"],
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 1
        assert chunks[0].metadata["role"] == "user"
        assert chunks[0].metadata["index_for_retrieval"] is True

    def test_assistant_chunks_tagged_not_for_retrieval(self, tmp_path):
        doc = _make_chatgpt_doc(
            messages=[
                "Assistant: You're working on the Ember personal intelligence system.",
            ],
            roles=["assistant"],
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 1
        assert chunks[0].metadata["role"] == "assistant"
        assert chunks[0].metadata["index_for_retrieval"] is False

    def test_system_role_defaults_to_indexed(self, tmp_path):
        doc = _make_chatgpt_doc(
            messages=["System: You are a helpful assistant with these rules."],
            roles=["system"],
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 1
        assert chunks[0].metadata["role"] == "system"
        assert chunks[0].metadata["index_for_retrieval"] is True

    def test_user_chunk_written_flat_and_indexed(self, tmp_path):
        doc = _make_chatgpt_doc(
            messages=["User: What's on my plate this week for the project?"],
            roles=["user"],
        )
        chunks = chunk_document(doc)

        with patch("src.ingest.writers.embed_text", return_value=[0.1, 0.2, 0.3]) as m_embed:
            write_chunks_to_vault(chunks, str(tmp_path))

        flat = _read_flat_chunks(tmp_path)
        index = _read_index(tmp_path)

        assert len(flat) == 1
        assert flat[0]["metadata"]["role"] == "user"
        assert len(index) == 1
        assert index[0]["metadata"]["role"] == "user"
        assert m_embed.call_count == 1

    def test_assistant_chunk_written_flat_but_not_indexed(self, tmp_path):
        doc = _make_chatgpt_doc(
            messages=[
                "Assistant: You have three active projects based on our prior discussion.",
            ],
            roles=["assistant"],
        )
        chunks = chunk_document(doc)

        with patch("src.ingest.writers.embed_text", return_value=[0.1, 0.2, 0.3]) as m_embed:
            write_chunks_to_vault(chunks, str(tmp_path))

        flat = _read_flat_chunks(tmp_path)
        index = _read_index(tmp_path)

        assert len(flat) == 1, "assistant chunk must still be written flat"
        assert flat[0]["metadata"]["role"] == "assistant"
        assert index == [], "assistant chunk must NOT enter the vector index"
        assert m_embed.call_count == 0, "embed_text must not be called for assistant chunks"

    def test_mixed_conversation_only_user_chunks_indexed(self, tmp_path):
        doc = _make_chatgpt_doc(
            messages=[
                "User: I've been thinking about switching jobs lately.",
                "Assistant: That's a big decision. What's driving the thought?",
                "User: The commute is wearing me down and the work is boring.",
                "Assistant: Those are two distinct problems worth separating.",
            ],
            roles=["user", "assistant", "user", "assistant"],
        )
        chunks = chunk_document(doc)
        assert len(chunks) == 4

        with patch("src.ingest.writers.embed_text", return_value=[0.1, 0.2, 0.3]) as m_embed:
            write_chunks_to_vault(chunks, str(tmp_path))

        flat = _read_flat_chunks(tmp_path)
        index = _read_index(tmp_path)

        assert len(flat) == 4
        flat_roles = sorted(r["metadata"]["role"] for r in flat)
        assert flat_roles == ["assistant", "assistant", "user", "user"]

        assert len(index) == 2
        assert all(entry["metadata"]["role"] == "user" for entry in index)
        assert m_embed.call_count == 2

    def test_reimport_idempotent_no_assistant_leakage(self, tmp_path):
        """Re-importing the same export must never add assistant embeddings."""
        doc = _make_chatgpt_doc(
            messages=[
                "User: What do I usually eat for breakfast on weekdays?",
                "Assistant: You typically mentioned oatmeal and coffee.",
            ],
            roles=["user", "assistant"],
        )
        chunks = chunk_document(doc)

        with patch("src.ingest.writers.embed_text", return_value=[0.1, 0.2, 0.3]):
            write_chunks_to_vault(chunks, str(tmp_path))
            write_chunks_to_vault(chunks, str(tmp_path))

        index = _read_index(tmp_path)
        assert len(index) == 1
        assert index[0]["metadata"]["role"] == "user"

    def test_chunk_without_metadata_key_defaults_to_indexed(self, tmp_path):
        """Backward-compat: chunks from other chunkers (no key set) still index."""
        chunk = ChunkedDocument(
            source="manual",
            doc_id="doc_manual_0",
            chunk_id="doc_manual_0_chunk_0",
            title="Manual",
            created_at="2026-04-20",
            content="Some text without the index_for_retrieval key present.",
            metadata={},
        )
        with patch("src.ingest.writers.embed_text", return_value=[0.1, 0.2, 0.3]) as m_embed:
            write_chunks_to_vault([chunk], str(tmp_path))

        index = _read_index(tmp_path)
        assert len(index) == 1
        assert m_embed.call_count == 1

    def test_skip_log_emitted_for_assistant_chunk(self, tmp_path, caplog):
        """Runtime log line confirms the gate executes on the skip path."""
        doc = _make_chatgpt_doc(
            messages=["Assistant: GPT said something that is not user memory."],
            roles=["assistant"],
        )
        chunks = chunk_document(doc)

        with caplog.at_level("INFO", logger="ember.ingest.writers"):
            with patch("src.ingest.writers.embed_text", return_value=[0.1]):
                write_chunks_to_vault(chunks, str(tmp_path))

        assert any(
            "Skipping vector index" in record.message
            for record in caplog.records
        ), "expected skip log line not emitted"
