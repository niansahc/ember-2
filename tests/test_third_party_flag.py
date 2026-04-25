"""
tests/test_third_party_flag.py

Coverage for contains_named_third_party() heuristic and the write-time
integration in src/memory/write_memory.py (per ADR-021 prerequisite).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.memory.third_party_detection import contains_named_third_party


# ---------------------------------------------------------------------------
# Heuristic — positive cases
# ---------------------------------------------------------------------------


def test_kinship_marker_partner_detected() -> None:
    assert contains_named_third_party("my partner said we should leave early")


def test_kinship_marker_mom_detected() -> None:
    assert contains_named_third_party("My mom called yesterday")


def test_kinship_marker_coworker_detected() -> None:
    assert contains_named_third_party("my coworker is being weird about it")


def test_reported_speech_she_said_detected() -> None:
    assert contains_named_third_party("she said it would be fine")


def test_reported_speech_they_think_detected() -> None:
    assert contains_named_third_party("they think the deadline is tight")


def test_reported_speech_he_believed_detected() -> None:
    assert contains_named_third_party("He believed the original plan was better")


def test_named_proper_noun_speech_detected() -> None:
    assert contains_named_third_party("Sam told me yesterday it was done")


def test_named_proper_noun_thinks_detected() -> None:
    assert contains_named_third_party("Alex thinks the design is overengineered")


# ---------------------------------------------------------------------------
# Heuristic — negative cases
# ---------------------------------------------------------------------------


def test_first_person_only_not_detected() -> None:
    assert not contains_named_third_party(
        "I went to the store and got groceries"
    )


def test_third_person_no_speech_marker_not_detected() -> None:
    assert not contains_named_third_party("the weather was nice today")


def test_my_plus_inanimate_not_detected() -> None:
    """`my plan`, `my opinion`, `my work` should not match — only kinship."""
    assert not contains_named_third_party("my plan for tomorrow is to rest")
    assert not contains_named_third_party("my opinion is that we should wait")
    assert not contains_named_third_party("my work has been overwhelming")


def test_empty_string_not_detected() -> None:
    assert not contains_named_third_party("")


def test_pronoun_without_speech_verb_not_detected() -> None:
    """`she walked` is not reported speech — should NOT trigger."""
    assert not contains_named_third_party("she walked into the room")


def test_capitalized_word_without_speech_verb_not_detected() -> None:
    """Sentence-initial capitalization without a speech verb is safe."""
    assert not contains_named_third_party("Tomorrow looks busy")


# ---------------------------------------------------------------------------
# Integration — write_memory sets the flag for conversation type
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_vault(tmp_path, monkeypatch):
    """Override PRIVATE_VAULT_PATH to a tmp dir for isolated write tests."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "memory").mkdir()
    (vault / "embeddings").mkdir()
    monkeypatch.setenv("PRIVATE_VAULT_PATH", str(vault))
    # Re-import config so it picks up the env override.
    import importlib
    import src.core.config as cfg
    importlib.reload(cfg)
    yield vault


def _read_record(file_path: Path) -> dict:
    import json
    return json.loads(file_path.read_text(encoding="utf-8"))


def test_write_memory_sets_flag_for_conversation_type(temp_vault, monkeypatch) -> None:
    """conversation type with third-party content -> flag True in metadata."""
    # Stub embed_text to avoid Ollama dependency in tests.
    monkeypatch.setattr(
        "src.memory.write_memory.embed_text", lambda _text: [0.0] * 768
    )
    from src.memory.write_memory import write_memory

    file_path = write_memory(
        text="my partner said we should rebook the flight",
        memory_type="conversation",
        source="test",
    )
    assert file_path is not None
    record = _read_record(file_path)
    assert record["metadata"]["contains_named_third_party"] is True


def test_write_memory_sets_flag_false_when_no_third_party(temp_vault, monkeypatch) -> None:
    """conversation type with self-only content -> flag False in metadata."""
    monkeypatch.setattr(
        "src.memory.write_memory.embed_text", lambda _text: [0.0] * 768
    )
    from src.memory.write_memory import write_memory

    file_path = write_memory(
        text="I went for a walk this morning",
        memory_type="conversation",
        source="test",
    )
    assert file_path is not None
    record = _read_record(file_path)
    assert record["metadata"]["contains_named_third_party"] is False


def test_write_memory_skips_flag_for_non_conversation_type(temp_vault, monkeypatch) -> None:
    """journal/profile/etc. -> no flag added (not in scope for ADR-021)."""
    monkeypatch.setattr(
        "src.memory.write_memory.embed_text", lambda _text: [0.0] * 768
    )
    from src.memory.write_memory import write_memory

    file_path = write_memory(
        text="my partner said we should rebook the flight",
        memory_type="journal",
        source="test",
    )
    assert file_path is not None
    record = _read_record(file_path)
    assert "contains_named_third_party" not in record["metadata"]


def test_write_memory_caller_override_wins_over_heuristic(temp_vault, monkeypatch) -> None:
    """If caller passes the flag explicitly, heuristic does not override."""
    monkeypatch.setattr(
        "src.memory.write_memory.embed_text", lambda _text: [0.0] * 768
    )
    from src.memory.write_memory import write_memory

    file_path = write_memory(
        text="my partner said we should rebook the flight",
        memory_type="conversation",
        source="test",
        metadata={"contains_named_third_party": False},
    )
    assert file_path is not None
    record = _read_record(file_path)
    assert record["metadata"]["contains_named_third_party"] is False
