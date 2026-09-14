"""
tests/test_provenance_resolver.py

ADR-015 amendment (PR #180), implementation step 1: provenance emission for
every derived-record writer, and the cross-type resolver that reads it back.

Runs against the session-scoped isolated test vault from conftest.py rather
than a local temp_vault fixture -- that fixture is already active and
autouse for every test, so a second override is unnecessary duplication of
what the two older provenance test files (test_generate_reflection_provenance
.py, test_lodestone_synthesis.py) each built for themselves before that
mechanism existed.
"""

from __future__ import annotations

import json

import pytest

from src.core.config import get_private_vault_path
from src.memory.resolve_memory import (
    find_conversation_ids_for_session,
    resolve_source_records,
)
from src.memory.service import MemoryService
from src.memory.write_memory import flatten_metadata, write_memory


@pytest.fixture(autouse=True)
def stub_embeddings(monkeypatch):
    """write_memory always embeds; no Ollama dependency for these tests."""
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)


def _write(memory_type: str, text: str, metadata: dict | None = None) -> str:
    """write_memory a record and return its id (== the file stem)."""
    path = write_memory(text=text, memory_type=memory_type, source="test", metadata=metadata)
    assert path is not None, f"write_memory returned None for {memory_type!r}: {text!r}"
    return path.stem


class TestResolveSourceRecords:
    def test_resolves_across_all_four_candidate_types(self):
        journal_id = _write("journal", "A journal entry that is long enough to clear the write floor.")
        conversation_id = _write("conversation", "A conversation turn.")
        reflection_id = _write("reflection", "A reflection record that is long enough to clear the write floor.")
        ingested_id = _write("ingested", "An ingested chunk of text that is long enough to clear the write floor.")

        resolved = resolve_source_records(
            [journal_id, conversation_id, reflection_id, ingested_id]
        )

        resolved_ids = {r["id"] for r in resolved}
        assert resolved_ids == {journal_id, conversation_id, reflection_id, ingested_id}

    def test_ingested_chunk_with_no_body_id_field_resolves_via_filename(self):
        """The real ingest pipeline (src/ingest/writers.py) writes chunks with
        no top-level "id" key -- only "chunk_id" -- relying on the
        filename-equals-id convention. resolve_source_records must normalize
        that on read rather than assuming every record body carries "id"."""
        vault = get_private_vault_path()
        ingested_dir = vault / "memory" / "ingested"
        ingested_dir.mkdir(parents=True, exist_ok=True)
        chunk_id = "2026-01-01T00-00-00-000000"
        (ingested_dir / f"{chunk_id}.json").write_text(
            json.dumps({
                "type": "ingested",
                "source": "test_import",
                "doc_id": "doc-1",
                "chunk_id": chunk_id,
                "content": "chunk body text",
                "metadata": {},
            }),
            encoding="utf-8",
        )

        resolved = resolve_source_records([chunk_id])

        assert len(resolved) == 1
        assert resolved[0]["id"] == chunk_id

    def test_unresolvable_id_is_skipped_not_raised(self):
        real_id = _write("journal", "A resolvable journal record for this test.")

        resolved = resolve_source_records(["nonexistent-id-0001", real_id])

        assert len(resolved) == 1
        assert resolved[0]["id"] == real_id

    def test_empty_list_resolves_to_empty_list(self):
        assert resolve_source_records([]) == []

    def test_blank_id_in_list_is_skipped(self):
        real_id = _write("journal", "Another resolvable journal record for this test.")
        resolved = resolve_source_records(["", real_id])
        assert [r["id"] for r in resolved] == [real_id]


class TestFindConversationIdsForSession:
    def test_returns_only_matching_session(self):
        a1 = _write("conversation", "Session A turn one.", {"session_id": "sess-a"})
        a2 = _write("conversation", "Session A turn two.", {"session_id": "sess-a"})
        _write("conversation", "Session B turn one.", {"session_id": "sess-b"})

        ids = find_conversation_ids_for_session("sess-a")

        assert set(ids) == {a1, a2}

    def test_no_matching_session_returns_empty(self):
        _write("conversation", "Some unrelated turn.", {"session_id": "sess-x"})
        assert find_conversation_ids_for_session("sess-nonexistent") == []

    def test_none_session_id_returns_empty(self):
        assert find_conversation_ids_for_session(None) == []

    def test_empty_string_session_id_returns_empty(self):
        assert find_conversation_ids_for_session("") == []


class TestSessionReflectionProvenance:
    def test_emits_source_record_ids_resolvable_to_real_conversation_records(self, monkeypatch):
        from src.reflection.session_reflection import generate_session_reflection

        session_id = "sess-reflect-1"
        conv_id_1 = _write("conversation", "First turn of the session.", {"session_id": session_id})
        conv_id_2 = _write("conversation", "Second turn of the session.", {"session_id": session_id})

        monkeypatch.setattr(
            "src.reflection.session_reflection.ollama.chat",
            lambda **kwargs: {"message": {"content": "A narrative session reflection covering what was discussed."}},
        )

        buffer = [
            {"user": "What should I focus on?", "assistant": "The retrieval pipeline."},
            {"user": "Let's fix the profile bug.", "assistant": "Walking through it now."},
            {"user": "That worked, what next?", "assistant": "Run the eval harness."},
        ]
        result = generate_session_reflection(buffer, session_id=session_id)
        assert result is not None

        vault = get_private_vault_path()
        reflection_files = sorted((vault / "memory" / "reflection").glob("*.json"))
        assert reflection_files, "no reflection record was written"
        record = json.loads(reflection_files[-1].read_text(encoding="utf-8"))

        source_ids = record["metadata"]["source_record_ids"]
        assert set(source_ids) == {conv_id_1, conv_id_2}

        resolved = resolve_source_records(source_ids)
        assert {r["id"] for r in resolved} == {conv_id_1, conv_id_2}

    def test_none_session_id_emits_empty_source_record_ids(self, monkeypatch):
        from src.reflection.session_reflection import generate_session_reflection

        monkeypatch.setattr(
            "src.reflection.session_reflection.ollama.chat",
            lambda **kwargs: {"message": {"content": "A narrative session reflection covering what was discussed."}},
        )

        buffer = [
            {"user": "Message one.", "assistant": "Reply one."},
            {"user": "Message two.", "assistant": "Reply two."},
            {"user": "Message three.", "assistant": "Reply three."},
        ]
        result = generate_session_reflection(buffer, session_id=None)
        assert result is not None

        vault = get_private_vault_path()
        reflection_files = sorted((vault / "memory" / "reflection").glob("*.json"))
        record = json.loads(reflection_files[-1].read_text(encoding="utf-8"))

        assert record["metadata"]["source_record_ids"] == []


class TestSessionSummaryProvenance:
    def test_emits_source_record_ids_resolvable_to_real_conversation_records(self):
        from src.reflection.session_summary import write_session_summary

        session_id = "sess-summary-1"
        conv_id = _write("conversation", "A compressed conversation turn.", {"session_id": session_id})

        service = MemoryService()
        write_session_summary(
            memory_service=service,
            summary="A compression summary of the older turns in this conversation session.",
            turns_compressed=2,
            session_id=session_id,
        )

        vault = get_private_vault_path()
        reflection_files = sorted((vault / "memory" / "reflection").glob("*.json"))
        assert reflection_files, "no reflection record was written"
        record = json.loads(reflection_files[-1].read_text(encoding="utf-8"))

        assert record["metadata"]["source_record_ids"] == [conv_id]

    def test_none_session_id_emits_empty_source_record_ids(self):
        from src.reflection.session_summary import write_session_summary

        service = MemoryService()
        write_session_summary(
            memory_service=service,
            summary="A compression summary with no session correlator available at all.",
            turns_compressed=2,
            session_id=None,
        )

        vault = get_private_vault_path()
        reflection_files = sorted((vault / "memory" / "reflection").glob("*.json"))
        record = json.loads(reflection_files[-1].read_text(encoding="utf-8"))

        assert record["metadata"]["source_record_ids"] == []


class TestFlattenMetadataTruncationExemption:
    def test_source_record_ids_survives_more_than_twenty_entries(self):
        wide_ids = [f"id-{i}" for i in range(30)]
        flattened = flatten_metadata({"source_record_ids": wide_ids})
        assert flattened["source_record_ids"] == wide_ids

    def test_unrelated_list_field_still_truncated_at_twenty(self):
        """Confirms the exemption is scoped to source_record_ids, not a
        blanket relaxation of the truncation for every list metadata field."""
        wide_list = [f"tag-{i}" for i in range(30)]
        flattened = flatten_metadata({"some_other_list": wide_list})
        assert flattened["some_other_list"] == wide_list[:20]


class TestEveryDerivedWriterEmitsProvenance:
    """Grep guard, explicit-file-list style (matching
    test_b_ret_001_dead_code_retired.py's pattern): the four writers that
    produce derived reflection/lodestone records must each reference
    source_record_ids. Catches a future edit that quietly removes the field
    from one writer rather than relying only on behavioral tests to notice."""

    WRITER_FILES = (
        "src/reflection/generate_reflection.py",
        "src/reflection/lodestone_synthesis.py",
        "src/reflection/session_reflection.py",
        "src/reflection/session_summary.py",
    )

    def test_all_writers_reference_source_record_ids(self):
        import os

        repo_root = os.path.join(os.path.dirname(__file__), "..")
        missing = []
        for rel_path in self.WRITER_FILES:
            full_path = os.path.join(repo_root, rel_path)
            with open(full_path, encoding="utf-8") as f:
                content = f.read()
            if "source_record_ids" not in content:
                missing.append(rel_path)

        assert not missing, f"writers missing source_record_ids: {missing}"
