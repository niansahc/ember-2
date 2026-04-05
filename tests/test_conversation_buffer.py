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
- set_context_window() — known and unknown models
- format_for_prompt() — empty and populated
- end-to-end compression cycle (pop → inject → buffer state)
"""

import pytest

from src.context.conversation_buffer import (
    ConversationBuffer,
    MODEL_CONTEXT_WINDOWS,
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
# set_context_window
# ---------------------------------------------------------------------------

def test_set_context_window_known_model():
    buf = ConversationBuffer(context_window=8192)
    buf.set_context_window("qwen2.5:14b")
    assert buf.context_window == MODEL_CONTEXT_WINDOWS["qwen2.5:14b"]


def test_set_context_window_all_known_models():
    for model, window in MODEL_CONTEXT_WINDOWS.items():
        buf = ConversationBuffer()
        buf.set_context_window(model)
        assert buf.context_window == window


def test_set_context_window_unknown_model_unchanged():
    buf = ConversationBuffer(context_window=8192)
    buf.set_context_window("some-unknown-model:99b")
    assert buf.context_window == 8192


def test_set_context_window_phi3_mini_is_smallest():
    buf = ConversationBuffer()
    buf.set_context_window("phi3:mini")
    assert buf.context_window == 4096


def test_set_context_window_qwen_is_largest():
    buf = ConversationBuffer()
    buf.set_context_window("qwen2.5:14b")
    assert buf.context_window == 32768


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
