"""
tests/test_clarification_session_filter.py

The B2 next-turn check looks for this session's most recent assistant turn
and asks whether it was a clarification (awaiting_search_content). It used
to read the 5 newest conversation records vault-wide and only then filter
by session_id, so five newer turns from any other conversation pushed the
clarification out of the window and the follow-up was classified in
isolation instead of dispatched to web search (the slice-then-filter
defect of ultrareview #280 finding 5, at a second call site).

read_memories(session_id=...) now filters before the limit, and the check
reads through it.

Runs against the session-scoped isolated test vault from conftest.py with
synthetic records only.
"""

from __future__ import annotations

import uuid

import pytest

from src.api.openai_adapter import _prior_turn_awaits_search_content
from src.memory.read_memory import read_memories
from src.memory.write_memory import write_memory

pytestmark = pytest.mark.usefixtures("stub_write_embeddings")


def _session_id() -> str:
    return f"sess_test_{uuid.uuid4().hex[:8]}"


def _write_turn(session_id: str, role: str, text: str, **extra) -> str:
    path = write_memory(
        text=text, memory_type="conversation", source="test",
        metadata={"session_id": session_id, "role": role, **extra},
    )
    assert path is not None
    return path.stem


def _write_clarification(session_id: str) -> str:
    return _write_turn(
        session_id, "assistant",
        "What would you like me to search for on the web today?",
        awaiting_search_content=True,
    )


def _write_other_traffic(turns: int = 6) -> None:
    other = _session_id()
    for i in range(turns):
        role = "user" if i % 2 == 0 else "assistant"
        _write_turn(other, role, f"Unrelated synthetic turn number {i} in another conversation.")


def test_clarification_found_when_newer_turns_from_other_sessions_exceed_limit():
    sid = _session_id()
    _write_clarification(sid)
    _write_other_traffic(6)

    assert _prior_turn_awaits_search_content(sid) is True


def test_clarification_in_another_session_does_not_leak():
    sid = _session_id()
    _write_turn(sid, "assistant", "Here is a plain answer with no clarification attached.")
    _write_clarification(_session_id())

    assert _prior_turn_awaits_search_content(sid) is False


def test_control_clarification_in_own_session_is_found():
    """Positive control for the no-leak test: the same flagged turn written
    in this session is detected, so the False above is not vacuous."""
    sid = _session_id()
    _write_turn(sid, "assistant", "Here is a plain answer with no clarification attached.")
    _write_clarification(sid)

    assert _prior_turn_awaits_search_content(sid) is True


def test_read_memories_session_filter_applies_before_limit():
    sid = _session_id()
    own = [_write_turn(sid, "user", f"Own conversation synthetic turn {i}.") for i in range(3)]
    _write_other_traffic(6)

    records = read_memories(memory_type="conversation", limit=2, session_id=sid)

    assert [r["id"] for r in records] == [own[2], own[1]]
