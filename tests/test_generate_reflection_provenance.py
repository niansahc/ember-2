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



# ---------------------------------------------------------------------------
# Conversation records pass the index filter before they can be candidates
# (ADR-047, plan v3 Q6): one filter for every derived artifact.
# ---------------------------------------------------------------------------

CODE_FENCE_TURN = (
    "Here is my config:\n```yaml\nkey: value\n```\n"
    "Why does this fail at startup every single time I try it?"
)
PLAIN_TURN = (
    "I keep postponing the garden plans because the weekends fill up with errands."
)
# Passes reflection's own skip markers; only the index filter rejects it
# (text starting with "{").
JSON_TURN = (
    '{"plan": "water the garden on weekends", "status": "postponed again this month"}'
)


@pytest.fixture
def bound_vault(tmp_path):
    from src.core.config import vault_binding

    (tmp_path / "memory").mkdir()
    with vault_binding(tmp_path):
        yield tmp_path


def _conversation_turn(text: str) -> None:
    from src.memory.write_memory import write_canonical_record

    write_canonical_record(
        text=text, memory_type="conversation", source="chat",
        metadata={"role": "user", "content_kind": "user_content",
                  "session_id": "sess_test_reflect_001"},
    )


def test_pasted_json_conversation_record_is_not_a_reflection_candidate(bound_vault):
    from src.reflection.generate_reflection import generate_reflection

    _conversation_turn(JSON_TURN)
    result = generate_reflection(memory_types=["conversation"], store=False, prompt_template=None)
    assert result["memory_count"] == 0


def test_code_fence_conversation_record_is_not_a_reflection_candidate(bound_vault):
    """Reflection's own skip markers already drop code fences; the shared
    filter must keep it that way."""
    from src.reflection.generate_reflection import generate_reflection

    _conversation_turn(CODE_FENCE_TURN)
    result = generate_reflection(memory_types=["conversation"], store=False, prompt_template=None)
    assert result["memory_count"] == 0


def test_plain_conversation_record_is_a_reflection_candidate(bound_vault):
    """Positive control for the two absences above."""
    from src.reflection.generate_reflection import generate_reflection

    _conversation_turn(PLAIN_TURN)
    result = generate_reflection(memory_types=["conversation"], store=False, prompt_template=None)
    assert result["memory_count"] == 1
    assert "garden plans" in result["summary"]
