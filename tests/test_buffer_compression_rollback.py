"""tests/test_buffer_compression_rollback.py

Unit tests for the failure path in LLMAdapter._maybe_compress_buffer.

Context: the buffer is mutated (pop_oldest_half) BEFORE the LLM
summarization call. Prior to the rollback fix, an exception in the
summarization call would leave the popped turns dropped permanently
with no log line -- the async daemon-thread call site swallows
exceptions silently. The fix wraps the LLM call in try/except and
re-prepends the popped turns to the buffer on failure.

These tests pin:
  - Buffer length is preserved on summarization failure.
  - Turn order is preserved on rollback (oldest turns return to the head).
  - A structured [BUFFER] log line is emitted with the failure reason.
  - The happy path still works (turns popped, summary injected).
"""
from __future__ import annotations

import logging

from unittest.mock import MagicMock

from src.context.conversation_buffer import ConversationBuffer, COMPRESSION_THRESHOLD
from src.llm.adapter import LLMAdapter


def _adapter_with_real_buffer() -> LLMAdapter:
    """Build a bare LLMAdapter wired to a real ConversationBuffer plus
    mocks for everything _maybe_compress_buffer touches. Bypasses
    __init__ to avoid loading the full service graph."""
    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "test-model"
    adapter.prompt_builder = MagicMock()
    adapter.prompt_builder.conversation_buffer = ConversationBuffer()
    adapter.memory_service = MagicMock()
    return adapter


def _fill_buffer_past_threshold(buf: ConversationBuffer) -> None:
    """Add enough long turns to trip needs_compression()."""
    long_msg = " ".join(f"word{j}" for j in range(100))
    for _ in range(8):
        buf.add_turn(long_msg, long_msg)
    assert buf.needs_compression() is True


def test_compression_failure_restores_turns(monkeypatch, caplog) -> None:
    """When the summarization LLM call raises, the popped turns must be
    restored to the buffer and a structured log line must be emitted.
    No turns are silently lost."""
    adapter = _adapter_with_real_buffer()
    buf = adapter.prompt_builder.conversation_buffer
    _fill_buffer_past_threshold(buf)

    pre_compression_turns = list(buf.get_recent())
    pre_compression_len = len(pre_compression_turns)

    def _raise(_prompt: str) -> str:
        raise RuntimeError("simulated ollama failure")

    monkeypatch.setattr(adapter, "_summarize_with_plain_prompt", _raise)

    with caplog.at_level(logging.WARNING, logger="src.llm.adapter"):
        adapter._maybe_compress_buffer()

    # Buffer length preserved -- nothing silently dropped.
    assert len(buf.get_recent()) == pre_compression_len
    # Turn ordering preserved (oldest turns prepended back at the head).
    assert buf.get_recent() == pre_compression_turns
    # A structured log line surfaces the failure for diagnosis.
    assert any(
        "[BUFFER] Compression failed" in r.message
        for r in caplog.records
    )
    # vault write must NOT have fired -- there was no summary to persist.
    adapter.memory_service.assert_not_called()


def test_compression_success_path_unchanged(monkeypatch) -> None:
    """Happy path: summarization succeeds, oldest turns are replaced by a
    synthetic summary turn at index 0, and the surviving recent turns are
    intact."""
    adapter = _adapter_with_real_buffer()
    buf = adapter.prompt_builder.conversation_buffer
    _fill_buffer_past_threshold(buf)

    pre_turns = list(buf.get_recent())
    expected_popped = len(pre_turns) // 2
    expected_surviving = pre_turns[expected_popped:]

    monkeypatch.setattr(
        adapter,
        "_summarize_with_plain_prompt",
        lambda _prompt: "synthetic summary text",
    )
    # Stub the vault write so we do not touch the real session_summary
    # persistence layer in a unit test.
    monkeypatch.setattr(
        "src.llm.adapter.write_session_summary",
        lambda **kwargs: None,
    )

    adapter._maybe_compress_buffer()

    turns = buf.get_recent()
    assert turns[0]["user"] == "[Earlier conversation summary]"
    assert turns[0]["assistant"] == "synthetic summary text"
    # Surviving turns follow the synthetic summary in original order.
    assert turns[1:] == expected_surviving


def test_compression_skips_when_below_threshold() -> None:
    """needs_compression() False short-circuit: no pop, no LLM call,
    no log line."""
    adapter = _adapter_with_real_buffer()
    buf = adapter.prompt_builder.conversation_buffer
    buf.add_turn("hello", "hi")
    assert buf.token_count() < COMPRESSION_THRESHOLD

    # Set a sentinel that would explode if _summarize_with_plain_prompt
    # was actually called.
    def _explode(_prompt: str) -> str:
        raise AssertionError("summarization should not run below threshold")

    adapter._summarize_with_plain_prompt = _explode  # type: ignore[assignment]

    adapter._maybe_compress_buffer()

    assert len(buf.get_recent()) == 1
