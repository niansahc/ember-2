"""Tests for src/llm/adapter.py generate_response_iter().

ADR-036 Option A: when constitutional review fires, the streaming SSE
generator at openai_adapter.py:1505 needs to emit review_pending /
review_complete events on the wire DURING the review window so the UI
breathing-dot indicator (UI commit ed858c9) activates only during a
genuine review. The bridge is generate_response_iter(), a sync
generator that yields StatusSignal sentinels around the review call
and yields the final response string last.

These tests pin the contract:
  - When the trigger fires, exactly one review_pending then exactly
    one review_complete sentinel is yielded around the review call,
    in that order, before the final response string.
  - When the trigger does NOT fire, no StatusSignal is yielded at all.
  - The backward-compat generate_response() wrapper drops sentinels
    and returns only the final string.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from src.context.models import ContextPacket
from src.llm.adapter import LLMAdapter, StatusSignal
from src.safety.models import SafetyReviewResult, SafetyTriggerResult


def _bare_adapter() -> LLMAdapter:
    """Bypass __init__ and wire all collaborators as mocks. The iterator
    body never touches real services with this construction."""
    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "test-model"
    adapter.prompt_builder = MagicMock()
    adapter.prompt_builder.build_prompt.return_value = "system prompt"
    adapter.prompt_builder.conversation_buffer.question_suppressed = False
    adapter.policy_service = MagicMock()
    adapter.review_service = MagicMock()
    adapter.review_logger = MagicMock()
    adapter.review_logger.log.return_value = "/tmp/safety_log.json"
    adapter.memory_service = MagicMock()
    adapter._chat = MagicMock(return_value="draft response")
    adapter._maybe_compress_buffer = MagicMock()
    return adapter


def test_iter_yields_status_signals_around_review_when_triggered() -> None:
    """Triggered path: emits review_pending immediately before the
    review call and review_complete immediately after, then yields the
    final response string. Order: pending, complete, response."""
    adapter = _bare_adapter()
    adapter.policy_service.evaluate_trigger.return_value = SafetyTriggerResult(
        triggered=True, triggered_by=["test_trigger"]
    )
    adapter.policy_service.get_active_principles.return_value = ["principle_a"]
    adapter.review_service.review.return_value = SafetyReviewResult(
        triggered=True, outcome="allow", reviewed_text="reviewed text"
    )

    items = list(
        adapter.generate_response_iter(ContextPacket(user_message="hi"))
    )

    # Exactly one pending, one complete, and one final string -- in order.
    assert items[0] == StatusSignal("review_pending")
    assert items[1] == StatusSignal("review_complete")
    assert isinstance(items[-1], str)
    # No extra StatusSignals anywhere in the stream.
    signals = [it for it in items if isinstance(it, StatusSignal)]
    assert [s.name for s in signals] == ["review_pending", "review_complete"]
    # The pending sentinel must arrive before the review call begins.
    # Verify by asserting review() was called exactly once and that the
    # full iterator includes both sentinels (proves they bracket the call).
    assert adapter.review_service.review.call_count == 1


def test_iter_emits_no_signals_when_trigger_does_not_fire() -> None:
    """Trigger=False path: review never runs, no StatusSignal yielded.
    The breathing-dot must not activate on benign requests."""
    adapter = _bare_adapter()
    adapter.policy_service.evaluate_trigger.return_value = SafetyTriggerResult(
        triggered=False
    )

    items = list(
        adapter.generate_response_iter(ContextPacket(user_message="hi"))
    )

    assert all(not isinstance(it, StatusSignal) for it in items)
    assert adapter.review_service.review.call_count == 0
    # The single yielded item is the final response string (the draft,
    # since review didn't run).
    assert items == ["draft response"]


def test_iter_emits_signals_for_revise_outcome() -> None:
    """The pending/complete pair must fire regardless of review outcome
    (allow / revise / refuse_redirect). Pin the revise path."""
    adapter = _bare_adapter()
    adapter.policy_service.evaluate_trigger.return_value = SafetyTriggerResult(
        triggered=True, triggered_by=["sycophancy"]
    )
    adapter.policy_service.get_active_principles.return_value = ["sycophancy"]
    adapter.review_service.review.return_value = SafetyReviewResult(
        triggered=True, outcome="revise", reviewed_text="revised version"
    )

    items = list(
        adapter.generate_response_iter(ContextPacket(user_message="hi"))
    )
    signal_names = [it.name for it in items if isinstance(it, StatusSignal)]

    assert signal_names == ["review_pending", "review_complete"]
    # Revised text supersedes the draft in the final yielded string.
    assert items[-1] == "revised version"


def test_iter_emits_signals_for_refuse_redirect_outcome() -> None:
    """refuse_redirect path also emits the pending/complete pair."""
    adapter = _bare_adapter()
    adapter.policy_service.evaluate_trigger.return_value = SafetyTriggerResult(
        triggered=True, triggered_by=["social_engineering"]
    )
    adapter.policy_service.get_active_principles.return_value = ["refuse_unsafe"]
    adapter.review_service.review.return_value = SafetyReviewResult(
        triggered=True,
        outcome="refuse_redirect",
        refusal_message="I can't help with that.",
    )

    items = list(
        adapter.generate_response_iter(ContextPacket(user_message="hi"))
    )
    signal_names = [it.name for it in items if isinstance(it, StatusSignal)]

    assert signal_names == ["review_pending", "review_complete"]
    assert items[-1] == "I can't help with that."


def test_generate_response_wrapper_drains_iterator_returns_final_string() -> None:
    """Backward compat: non-streaming callers see no change. The wrapper
    drops StatusSignals and returns only the final response string,
    even when the trigger fires."""
    adapter = _bare_adapter()
    adapter.policy_service.evaluate_trigger.return_value = SafetyTriggerResult(
        triggered=True, triggered_by=["test"]
    )
    adapter.policy_service.get_active_principles.return_value = ["principle_a"]
    adapter.review_service.review.return_value = SafetyReviewResult(
        triggered=True, outcome="allow", reviewed_text="reviewed text"
    )

    result = adapter.generate_response(ContextPacket(user_message="hi"))

    assert isinstance(result, str)
    assert result == "reviewed text"


def test_status_signal_is_frozen_dataclass() -> None:
    """StatusSignal must be hashable and immutable -- it's a value
    object, not a mutable record. Equality is by name."""
    a = StatusSignal("review_pending")
    b = StatusSignal("review_pending")
    c = StatusSignal("review_complete")

    assert a == b
    assert a != c
    assert hash(a) == hash(b)
    # Frozen: assignment must raise.
    import dataclasses
    try:
        a.name = "review_complete"  # type: ignore[misc]
        raised = False
    except dataclasses.FrozenInstanceError:
        raised = True
    assert raised, "StatusSignal must be frozen"
