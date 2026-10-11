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

import pytest

from src.api.openai_adapter import _prior_turn_awaits_search_content
from src.memory.read_memory import read_memories
from tests.conftest import synthetic_session_id, write_conversation_turn

pytestmark = pytest.mark.usefixtures("stub_write_embeddings")


def _write_clarification(session_id: str) -> str:
    return write_conversation_turn(
        session_id, "What would you like me to search for on the web today?",
        role="assistant", awaiting_search_content=True,
    )


def _write_other_traffic(turns: int = 6) -> None:
    other = synthetic_session_id()
    for i in range(turns):
        role = "user" if i % 2 == 0 else "assistant"
        write_conversation_turn(
            other, f"Unrelated synthetic turn number {i} in another conversation.", role=role,
        )


def test_clarification_found_when_newer_turns_from_other_sessions_exceed_limit():
    sid = synthetic_session_id()
    _write_clarification(sid)
    _write_other_traffic(6)

    assert _prior_turn_awaits_search_content(sid) is True


def test_clarification_in_another_session_does_not_leak():
    sid = synthetic_session_id()
    write_conversation_turn(sid, "Here is a plain answer with no clarification attached.", role="assistant")
    _write_clarification(synthetic_session_id())

    assert _prior_turn_awaits_search_content(sid) is False


def test_control_clarification_in_own_session_is_found():
    """Positive control for the no-leak test: the same flagged turn written
    in this session is detected, so the False above is not vacuous."""
    sid = synthetic_session_id()
    write_conversation_turn(sid, "Here is a plain answer with no clarification attached.", role="assistant")
    _write_clarification(sid)

    assert _prior_turn_awaits_search_content(sid) is True


def test_read_memories_session_filter_applies_before_limit():
    sid = synthetic_session_id()
    own = [write_conversation_turn(sid, f"Own conversation synthetic turn {i}.", role="user") for i in range(3)]
    _write_other_traffic(6)

    records = read_memories(memory_type="conversation", limit=2, session_id=sid)

    assert [r["id"] for r in records] == [own[2], own[1]]
