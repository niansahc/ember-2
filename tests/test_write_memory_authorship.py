"""
tests/test_write_memory_authorship.py

Integration coverage for write_memory() setting the authorship column at
write time (Recalling Too Well Phase 1, item 2). Before this fix,
SqliteVectorStore.insert() did not accept or write the field at all, so
every record silently defaulted to 'unknown' regardless of what
classify_authorship() would have said.

Drives write_memory() against a real temp vault + real SqliteVectorStore,
per CLAUDE.md testing discipline (no mocking the filesystem vault).
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest


@pytest.fixture
def temp_vault(tmp_path, monkeypatch):
    """Override PRIVATE_VAULT_PATH to a tmp dir for isolated write tests."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "memory").mkdir()
    (vault / "embeddings").mkdir()
    monkeypatch.setenv("PRIVATE_VAULT_PATH", str(vault))
    import src.core.config as cfg
    importlib.reload(cfg)
    yield vault


def _authorship_for(vault, record_id: str) -> str:
    db_path = vault / "embeddings" / "memory.db"
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT authorship FROM vectors WHERE id = ?", (record_id,)
    ).fetchone()
    conn.close()
    assert row is not None, f"no row written for id {record_id}"
    return row[0]


def test_profile_write_is_first_person(temp_vault, monkeypatch):
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)
    from src.memory.write_memory import write_memory

    path = write_memory(
        text="Their name is Riley and they live in Portland.",
        memory_type="profile",
        source="onboarding",
    )
    assert path is not None
    assert _authorship_for(temp_vault, path.stem) == "first_person"


def test_journal_write_is_first_person(temp_vault, monkeypatch):
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)
    from src.memory.write_memory import write_memory

    path = write_memory(
        text="Long day today, but I got through the backlog I'd been dreading.",
        memory_type="journal",
        source="api",
    )
    assert path is not None
    assert _authorship_for(temp_vault, path.stem) == "first_person"


def test_reflection_from_reflection_engine_is_first_person(temp_vault, monkeypatch):
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)
    from src.memory.write_memory import write_memory

    path = write_memory(
        text="Recent themes: focus on the backlog, frustration with slow builds.",
        memory_type="reflection",
        source="reflection_engine",
    )
    assert path is not None
    assert _authorship_for(temp_vault, path.stem) == "first_person"


def test_reflection_from_unrecognized_source_is_unknown(temp_vault, monkeypatch):
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)
    from src.memory.write_memory import write_memory

    path = write_memory(
        text="Recent themes: something synthesized by a source we don't trust yet.",
        memory_type="reflection",
        source="some_unrecognized_source",
    )
    assert path is not None
    assert _authorship_for(temp_vault, path.stem) == "unknown"


def test_conversation_user_turn_is_first_person(temp_vault, monkeypatch):
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)
    from src.memory.write_memory import write_memory

    path = write_memory(
        text="I've been meaning to reorganize the garage all month.",
        memory_type="conversation",
        source="chat",
        metadata={"role": "user"},
    )
    assert path is not None
    assert _authorship_for(temp_vault, path.stem) == "first_person"


def test_conversation_assistant_turn_is_mixed(temp_vault, monkeypatch):
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)
    from src.memory.write_memory import write_memory

    path = write_memory(
        text="That sounds like a reasonable plan for the weekend project.",
        memory_type="conversation",
        source="chat",
        metadata={"role": "assistant"},
    )
    assert path is not None
    assert _authorship_for(temp_vault, path.stem) == "mixed"


def test_conversation_without_role_is_unknown(temp_vault, monkeypatch):
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)
    from src.memory.write_memory import write_memory

    path = write_memory(
        text="A conversation turn written without a role in its metadata.",
        memory_type="conversation",
        source="chat",
    )
    assert path is not None
    assert _authorship_for(temp_vault, path.stem) == "unknown"
