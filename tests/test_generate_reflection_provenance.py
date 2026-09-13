"""
tests/test_generate_reflection_provenance.py

Recalling Too Well Phase 1, item 1: generate_reflection() must record which
source records a reflection was compressed from, so a stale source can be
traced past the derived record that summarized it.

Drives generate_reflection() end to end against a real temp vault (no LLM
call -- the legacy concatenation path, prompt_template=None), per CLAUDE.md
testing discipline.
"""

from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture
def temp_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "memory").mkdir()
    (vault / "embeddings").mkdir()
    monkeypatch.setenv("PRIVATE_VAULT_PATH", str(vault))
    import src.core.config as cfg
    importlib.reload(cfg)
    yield vault


def _read_record(vault, memory_type, record_id):
    path = vault / "memory" / memory_type / f"{record_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_reflection_records_source_ids_of_selected_candidates(temp_vault, monkeypatch):
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)
    from src.memory.write_memory import write_memory
    from src.reflection.generate_reflection import generate_reflection

    written_ids = []
    for text in (
        "Worked on the backlog today and finally cleared the oldest tickets.",
        "Started planning the migration to the new retrieval pipeline this week.",
        "Noticed I've been more focused in the mornings than the afternoons lately.",
    ):
        path = write_memory(text=text, memory_type="journal", source="api")
        assert path is not None
        written_ids.append(path.stem)

    result = generate_reflection(memory_types="journal", store=True, prompt_template=None)
    assert result["memory_count"] > 0

    # Read the written reflection record back and check provenance.
    reflection_dir = temp_vault / "memory" / "reflection"
    files = sorted(reflection_dir.glob("*.json"))
    assert len(files) == 1
    reflection_record = json.loads(files[0].read_text(encoding="utf-8"))

    source_ids = reflection_record["metadata"]["source_record_ids"]
    assert source_ids, "source_record_ids should not be empty"
    # Every id in source_record_ids must be one of the journal records we wrote.
    assert set(source_ids).issubset(set(written_ids))


def test_no_candidates_selected_yields_no_write(temp_vault, monkeypatch):
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)
    from src.reflection.generate_reflection import generate_reflection

    result = generate_reflection(memory_types="journal", store=True, prompt_template=None)
    assert result["memory_count"] == 0

    reflection_dir = temp_vault / "memory" / "reflection"
    assert not reflection_dir.exists() or not list(reflection_dir.glob("*.json"))
