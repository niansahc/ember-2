"""
tests/test_conversation_ids_session_limit.py

find_conversation_ids_for_session() used to read the `limit` newest
conversation records vault-wide and only then filter by session_id. Any
session older than the newest `limit` turns resolved to no ids at all, so
its session reflection and session summary were written with an empty
source_record_ids (ultrareview #280, item 3).

Runs against the session-scoped isolated test vault from conftest.py. The
vault is shared across the test session, so other tests' conversation
records only push the older session further back; that strengthens the
failure the first test guards against rather than masking it.
"""

from __future__ import annotations

import uuid

import pytest

from src.memory.resolve_memory import find_conversation_ids_for_session
from src.memory.write_memory import write_memory


@pytest.fixture(autouse=True)
def stub_embeddings(monkeypatch):
    """write_memory always embeds; no Ollama dependency for these tests."""
    monkeypatch.setattr("src.memory.write_memory.embed_text", lambda _t: [0.0] * 768)


def _session_id() -> str:
    return f"sess_test_{uuid.uuid4().hex[:8]}"


def _write_turn(session_id: str, text: str) -> str:
    path = write_memory(
        text=text, memory_type="conversation", source="test",
        metadata={"session_id": session_id},
    )
    assert path is not None
    return path.stem


def test_older_session_resolves_when_newer_turns_exceed_limit():
    older = _session_id()
    newer = _session_id()
    older_ids = [_write_turn(older, f"Older session turn {i}.") for i in range(2)]
    for i in range(3):
        _write_turn(newer, f"Newer session turn {i}.")

    ids = find_conversation_ids_for_session(older, limit=2)

    assert set(ids) == set(older_ids)


def test_control_limit_still_bounds_a_long_session_to_its_newest_turns():
    """Positive control: the limit still applies, now per session. A
    four-turn session at limit=2 yields exactly its two newest ids."""
    sid = _session_id()
    turn_ids = [_write_turn(sid, f"Long session turn {i}.") for i in range(4)]

    ids = find_conversation_ids_for_session(sid, limit=2)

    assert set(ids) == set(turn_ids[-2:])
