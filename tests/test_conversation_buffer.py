"""
tests/test_conversation_buffer.py

Unit tests for ConversationBuffer in src/context/conversation_buffer.py.

Covers:
- add_turn() and get_recent()
- max_turns cap
- token_count() approximation
- needs_compression() threshold logic
- pop_oldest_half() — correct split, correct remainder
- inject_summary_turn() — prepended, structure preserved
- format_for_prompt() — empty and populated
- end-to-end compression cycle (pop → inject → buffer state)
"""

import pytest

from src.context.conversation_buffer import (
    ConversationBuffer,
    ConversationBufferStore,
    COMPRESSION_THRESHOLD,
    _estimate_tokens,
)


# ---------------------------------------------------------------------------
# _estimate_tokens
# ---------------------------------------------------------------------------

def test_estimate_tokens_empty():
    assert _estimate_tokens("") == 0


def test_estimate_tokens_single_word():
    # 1 word * 1.3 = 1 (int truncation)
    assert _estimate_tokens("hello") == 1


def test_estimate_tokens_ten_words():
    text = "one two three four five six seven eight nine ten"
    assert _estimate_tokens(text) == int(10 * 1.3)


def test_estimate_tokens_scales_with_length():
    short = "hello world"
    long = "hello world " * 10
    assert _estimate_tokens(long) > _estimate_tokens(short)


# ---------------------------------------------------------------------------
# add_turn / get_recent
# ---------------------------------------------------------------------------

def test_add_turn_stores_turn():
    buf = ConversationBuffer()
    buf.add_turn("hi", "hello")
    turns = buf.get_recent()
    assert len(turns) == 1
    assert turns[0] == {"user": "hi", "assistant": "hello"}


def test_add_turn_multiple_preserves_order():
    buf = ConversationBuffer()
    buf.add_turn("a", "1")
    buf.add_turn("b", "2")
    buf.add_turn("c", "3")
    turns = buf.get_recent()
    assert [t["user"] for t in turns] == ["a", "b", "c"]


def test_get_recent_returns_copy():
    buf = ConversationBuffer()
    buf.add_turn("x", "y")
    result = buf.get_recent()
    result.clear()
    assert len(buf.buffer) == 1


# ---------------------------------------------------------------------------
# max_turns cap
# ---------------------------------------------------------------------------

def test_max_turns_cap_drops_oldest():
    buf = ConversationBuffer(max_turns=3)
    buf.add_turn("a", "1")
    buf.add_turn("b", "2")
    buf.add_turn("c", "3")
    buf.add_turn("d", "4")  # should evict "a"
    turns = buf.get_recent()
    assert len(turns) == 3
    assert turns[0]["user"] == "b"
    assert turns[-1]["user"] == "d"


def test_max_turns_exact_boundary():
    buf = ConversationBuffer(max_turns=2)
    buf.add_turn("a", "1")
    buf.add_turn("b", "2")
    assert len(buf.get_recent()) == 2


# ---------------------------------------------------------------------------
# token_count
# ---------------------------------------------------------------------------

def test_token_count_empty_buffer():
    buf = ConversationBuffer()
    assert buf.token_count() == 0


def test_token_count_single_turn():
    buf = ConversationBuffer()
    buf.add_turn("hello world", "goodbye world")
    # _estimate_tokens truncates per string, not per total:
    # int(2 * 1.3) + int(2 * 1.3) = 2 + 2 = 4
    assert buf.token_count() == _estimate_tokens("hello world") + _estimate_tokens("goodbye world")


def test_token_count_accumulates_across_turns():
    buf = ConversationBuffer()
    buf.add_turn("one two", "three four")
    buf.add_turn("five six", "seven eight")
    # truncation is per-string: int(2*1.3)*4 = 2*4 = 8
    expected = sum(
        _estimate_tokens(t) for t in ["one two", "three four", "five six", "seven eight"]
    )
    assert buf.token_count() == expected


# ---------------------------------------------------------------------------
# needs_compression
# ---------------------------------------------------------------------------

def test_needs_compression_false_when_empty():
    buf = ConversationBuffer()
    assert buf.needs_compression() is False


def test_needs_compression_false_below_threshold():
    buf = ConversationBuffer()
    buf.add_turn("hello", "hi")
    assert buf.needs_compression() is False


def test_needs_compression_true_when_over_threshold():
    # Fixed threshold is 1500 tokens. Fill buffer past that.
    buf = ConversationBuffer()
    # Each turn ~200 words ≈ 260 tokens. 8 turns ≈ 2080 tokens > 1500.
    for i in range(8):
        long_msg = " ".join([f"word{j}" for j in range(100)])
        buf.add_turn(long_msg, long_msg)
    assert buf.needs_compression() is True


def test_needs_compression_boundary():
    # Just under threshold should not compress
    buf = ConversationBuffer()
    # COMPRESSION_THRESHOLD is 1500 tokens. ~1150 words = ~1500 tokens.
    # Add a few short turns that stay under.
    buf.add_turn("hello there", "hi how are you")
    assert buf.token_count() < COMPRESSION_THRESHOLD
    assert buf.needs_compression() is False


# ---------------------------------------------------------------------------
# pop_oldest_half
# ---------------------------------------------------------------------------

def test_pop_oldest_half_even_count():
    buf = ConversationBuffer()
    buf.add_turn("a", "1")
    buf.add_turn("b", "2")
    buf.add_turn("c", "3")
    buf.add_turn("d", "4")
    oldest = buf.pop_oldest_half()
    assert len(oldest) == 2
    assert oldest[0]["user"] == "a"
    assert oldest[1]["user"] == "b"
    remaining = buf.get_recent()
    assert len(remaining) == 2
    assert remaining[0]["user"] == "c"
    assert remaining[1]["user"] == "d"


def test_pop_oldest_half_odd_count():
    buf = ConversationBuffer()
    buf.add_turn("a", "1")
    buf.add_turn("b", "2")
    buf.add_turn("c", "3")
    oldest = buf.pop_oldest_half()
    assert len(oldest) == 1
    assert oldest[0]["user"] == "a"
    remaining = buf.get_recent()
    assert len(remaining) == 2


def test_pop_oldest_half_single_turn():
    buf = ConversationBuffer()
    buf.add_turn("only", "one")
    oldest = buf.pop_oldest_half()
    assert len(oldest) == 1
    assert len(buf.get_recent()) == 0


def test_pop_oldest_half_returns_correct_content():
    buf = ConversationBuffer()
    for i in range(6):
        buf.add_turn(f"u{i}", f"a{i}")
    oldest = buf.pop_oldest_half()
    assert [t["user"] for t in oldest] == ["u0", "u1", "u2"]
    assert [t["user"] for t in buf.get_recent()] == ["u3", "u4", "u5"]


# ---------------------------------------------------------------------------
# inject_summary_turn
# ---------------------------------------------------------------------------

def test_inject_summary_turn_prepends():
    buf = ConversationBuffer()
    buf.add_turn("real turn", "real response")
    buf.inject_summary_turn("this is a summary")
    turns = buf.get_recent()
    assert turns[0]["user"] == "[Earlier conversation summary]"
    assert turns[0]["assistant"] == "this is a summary"
    assert turns[1]["user"] == "real turn"


def test_inject_summary_turn_into_empty_buffer():
    buf = ConversationBuffer()
    buf.inject_summary_turn("summary of nothing")
    turns = buf.get_recent()
    assert len(turns) == 1
    assert turns[0]["assistant"] == "summary of nothing"


def test_inject_summary_turn_structure():
    buf = ConversationBuffer()
    buf.inject_summary_turn("key facts here")
    turn = buf.get_recent()[0]
    assert "user" in turn
    assert "assistant" in turn
    assert turn["assistant"] == "key facts here"


# ---------------------------------------------------------------------------
# format_for_prompt
# ---------------------------------------------------------------------------

def test_format_for_prompt_empty():
    buf = ConversationBuffer()
    assert buf.format_for_prompt() == "NO RECENT CONVERSATION"


def test_format_for_prompt_single_turn():
    buf = ConversationBuffer()
    buf.add_turn("hello", "hi there")
    result = buf.format_for_prompt()
    assert "User: hello" in result
    assert "Assistant: hi there" in result


def test_format_for_prompt_preserves_order():
    buf = ConversationBuffer()
    buf.add_turn("first", "response one")
    buf.add_turn("second", "response two")
    result = buf.format_for_prompt()
    assert result.index("first") < result.index("second")


# ---------------------------------------------------------------------------
# End-to-end compression cycle
# ---------------------------------------------------------------------------

def test_compression_cycle_pop_then_inject():
    buf = ConversationBuffer()
    for i in range(6):
        buf.add_turn(f"question {i}", f"answer {i}")

    oldest = buf.pop_oldest_half()
    assert len(oldest) == 3

    buf.inject_summary_turn("Summary of the first three exchanges.")

    turns = buf.get_recent()
    assert len(turns) == 4  # 1 summary + 3 remaining
    assert turns[0]["user"] == "[Earlier conversation summary]"
    assert turns[0]["assistant"] == "Summary of the first three exchanges."
    assert turns[1]["user"] == "question 3"


def test_compression_cycle_does_not_lose_recent_turns():
    buf = ConversationBuffer()
    for i in range(4):
        buf.add_turn(f"msg {i}", f"reply {i}")

    buf.pop_oldest_half()
    buf.inject_summary_turn("Earlier: discussed msg 0 and msg 1.")

    recent = buf.get_recent()
    users = [t["user"] for t in recent]
    assert "msg 2" in users
    assert "msg 3" in users
    assert "msg 0" not in users
    assert "msg 1" not in users


def test_needs_compression_false_after_compression():
    # After compressing, the buffer should be well under threshold
    buf = ConversationBuffer()
    for i in range(8):
        long_msg = " ".join([f"word{j}" for j in range(100)])
        buf.add_turn(long_msg, long_msg)
    assert buf.needs_compression() is True

    buf.pop_oldest_half()
    buf.inject_summary_turn("brief summary")
    # Now buffer has summary + 4 turns — should be under 1500
    assert buf.needs_compression() is False


# ---------------------------------------------------------------------------
# Session-aware reset (cross-session pollution prevention)
# ---------------------------------------------------------------------------

def test_session_change_clears_buffer():
    """A different session gets its own buffer: none of the prior session's
    turns, while the prior session keeps them (issue #133 replaced the
    reset-on-add_turn with per-session buffers)."""
    store = ConversationBufferStore()
    a = store.get(None, "sess_A")
    a.add_turn("a", "1")
    a.add_turn("b", "2")
    assert len(a.get_recent()) == 2

    b = store.get(None, "sess_B")
    b.add_turn("c", "3")
    turns = b.get_recent()
    assert len(turns) == 1
    assert turns[0]["user"] == "c"
    assert b.session_id == "sess_B"
    assert [t["user"] for t in store.get(None, "sess_A").get_recent()] == ["a", "b"]


def test_session_unchanged_preserves_buffer():
    """Same session across calls accumulates turns in one buffer. Positive
    control for test_session_change_clears_buffer."""
    store = ConversationBufferStore()
    for user, reply in (("a", "1"), ("b", "2"), ("c", "3")):
        store.get(None, "sess_X").add_turn(user, reply)
    turns = store.get(None, "sess_X").get_recent()
    assert [t["user"] for t in turns] == ["a", "b", "c"]


def test_session_id_none_preserves_legacy_behavior():
    """A buffer used without a session (the prompt builder's default buffer)
    accumulates turns with the 2-arg add_turn and never clears."""
    buf = ConversationBuffer()
    buf.add_turn("a", "1")
    buf.add_turn("b", "2")
    buf.add_turn("c", "3")
    assert len(buf.get_recent()) == 3
    assert buf.session_id is None


def test_session_change_clears_sticky_flags():
    """Session-sticky state (question_suppressed, declined_topics,
    hedged_record_ids) set in one conversation does not constrain another,
    and stays set in its own."""
    store = ConversationBufferStore()
    a = store.get(None, "sess_A")
    a.add_turn("stop asking me questions", "okay")
    assert a.question_suppressed is True
    a.mark_hedge_emitted(["rec_old_1", "rec_old_2"])
    assert len(a.hedged_record_ids) == 2

    b = store.get(None, "sess_B")
    b.add_turn("hello", "hi")
    assert b.question_suppressed is False
    assert b.declined_topics == []
    assert len(b.hedged_record_ids) == 0

    assert store.get(None, "sess_A").question_suppressed is True
    assert len(store.get(None, "sess_A").hedged_record_ids) == 2


def test_session_first_assignment_no_clear_on_empty():
    """The first get for a session creates an empty buffer that carries the
    session's identity."""
    store = ConversationBufferStore()
    assert store.find(None, "sess_first") is None
    buf = store.get(None, "sess_first")
    assert buf.session_id == "sess_first"
    assert buf.get_recent() == []
    buf.add_turn("hi", "hello")
    assert store.find(None, "sess_first") is buf
    assert len(buf.get_recent()) == 1


def test_session_change_logs_clearing(caplog):
    """Evicting a buffer at the store bound emits a [BUFFER] log line so a
    lost conversation history is visible in stdout for diagnosis."""
    import logging
    store = ConversationBufferStore(max_buffers=1)
    store.get(None, "sess_A").add_turn("a", "1")

    with caplog.at_level(logging.INFO, logger="ember.conversation_buffer"):
        store.get(None, "sess_B")

    assert any(
        "[BUFFER] Evicted" in r.message and "sess_A" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# ConversationBufferStore (issue #133)
# ---------------------------------------------------------------------------

def test_store_keys_by_vault_as_well_as_session(tmp_path):
    """The same session id in two vaults is two conversations."""
    store = ConversationBufferStore()
    store.get(tmp_path / "vault_one", "sess_A").add_turn("a", "1")
    assert store.get(tmp_path / "vault_two", "sess_A").get_recent() == []
    # Positive control: the same vault, spelled differently, is one key.
    same = store.get(str(tmp_path / "vault_one" / "."), "sess_A")
    assert [t["user"] for t in same.get_recent()] == ["a"]


def test_store_find_does_not_create():
    store = ConversationBufferStore()
    assert store.find(None, "sess_A") is None
    assert store.find(None, "sess_A") is None
    store.get(None, "sess_A")
    assert store.find(None, "sess_A") is not None


def test_store_evicts_least_recently_used():
    store = ConversationBufferStore(max_buffers=2)
    store.get(None, "sess_A")
    store.get(None, "sess_B")
    store.get(None, "sess_A")          # touch A, B is now oldest
    store.get(None, "sess_C")
    assert store.find(None, "sess_B") is None
    assert store.find(None, "sess_A") is not None
    assert store.find(None, "sess_C") is not None


def test_store_clear_drops_every_buffer():
    store = ConversationBufferStore()
    store.get(None, "sess_A").add_turn("a", "1")
    store.get(None, "sess_B").add_turn("b", "2")
    assert store.clear() == 2
    assert store.find(None, "sess_A") is None
    assert store.find(None, "sess_B") is None


def test_restore_turns_prepends_in_order():
    buf = ConversationBuffer()
    buf.add_turn("c", "3")
    buf.restore_turns([{"user": "a", "assistant": "1"}, {"user": "b", "assistant": "2"}])
    assert [t["user"] for t in buf.get_recent()] == ["a", "b", "c"]
