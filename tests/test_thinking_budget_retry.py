"""
tests/test_thinking_budget_retry.py

A thinking model's reasoning counts against num_predict. When the reasoning
uses the whole budget, Ollama stops with done_reason "length" and an empty
reply, and the turn would end in the empty-reply fallback. The adapter then
repeats that one call with think=False. Everything is mocked: no Ollama.
"""

from __future__ import annotations

from unittest.mock import patch

from src.llm.adapter import LLMAdapter
from src.llm.prompt_guardrail import OLLAMA_NUM_PREDICT


def _bare_adapter():
    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "qwen3.6:35b-a3b"
    return adapter


def _budget_spent():
    """The reply Ollama gives when the reasoning used the whole budget."""
    return {
        "message": {"content": "", "thinking": "reasoning " * 50},
        "done_reason": "length",
        "eval_count": OLLAMA_NUM_PREDICT,
    }


def _answer(text="A plain answer."):
    return {"message": {"content": text}, "done_reason": "stop", "eval_count": 12}


def _scripted(*replies):
    """fake ollama.chat returning `replies` in order; records each call."""
    calls: list[dict] = []
    queue = list(replies)

    def fake_chat(**kwargs):
        calls.append(kwargs)
        return queue.pop(0)

    return calls, fake_chat


def _chat(adapter, fake_chat, **kwargs):
    with patch("ollama.chat", side_effect=fake_chat), \
            patch.object(adapter, "_get_num_ctx", return_value=8192):
        return adapter._chat_ollama("system", "user", **kwargs)


def test_spent_budget_is_retried_without_thinking(caplog):
    adapter = _bare_adapter()
    calls, fake_chat = _scripted(_budget_spent(), _answer())
    with caplog.at_level("INFO", logger="ember.llm"):
        reply = _chat(adapter, fake_chat)
    assert reply == "A plain answer."
    assert len(calls) == 2
    assert "think" not in calls[0]
    assert calls[1]["think"] is False
    assert calls[1]["messages"] == calls[0]["messages"]
    assert calls[1]["options"] == calls[0]["options"]
    assert "[GEN] reasoning used the whole output budget" in caplog.text


def test_reply_with_text_is_one_call_without_think():
    """Absence test; test_spent_budget_is_retried_without_thinking is its
    positive control."""
    adapter = _bare_adapter()
    calls, fake_chat = _scripted(_answer())
    assert _chat(adapter, fake_chat) == "A plain answer."
    assert len(calls) == 1
    assert "think" not in calls[0]


def test_reply_cut_short_at_the_cap_is_not_retried():
    adapter = _bare_adapter()
    cut = {"message": {"content": "A reply cut", "thinking": "r"},
           "done_reason": "length", "eval_count": OLLAMA_NUM_PREDICT}
    calls, fake_chat = _scripted(cut, _answer())
    assert _chat(adapter, fake_chat) == "A reply cut"
    assert len(calls) == 1


def test_empty_reply_that_stopped_on_its_own_is_not_retried():
    adapter = _bare_adapter()
    empty_stop = {"message": {"content": ""}, "done_reason": "stop", "eval_count": 3}
    calls, fake_chat = _scripted(empty_stop, _answer())
    assert _chat(adapter, fake_chat) == ""
    assert len(calls) == 1


def test_call_already_without_thinking_is_not_retried():
    """The search-failure arm passes think=False: no second call."""
    adapter = _bare_adapter()
    calls, fake_chat = _scripted(_budget_spent(), _answer())
    assert _chat(adapter, fake_chat, think=False) == ""
    assert len(calls) == 1
    assert calls[0]["think"] is False


def test_think_true_is_retried_without_thinking():
    adapter = _bare_adapter()
    calls, fake_chat = _scripted(_budget_spent(), _answer())
    assert _chat(adapter, fake_chat, think=True) == "A plain answer."
    assert [c["think"] for c in calls] == [True, False]


def test_retry_keeps_the_assistant_prefix():
    adapter = _bare_adapter()
    calls, fake_chat = _scripted(_budget_spent(), _answer("the answer."))
    reply = _chat(adapter, fake_chat, assistant_prefix="Based on current search results, ")
    assert reply == "Based on current search results, the answer."
    assert calls[1]["messages"][-1] == {
        "role": "assistant", "content": "Based on current search results, "}


def test_second_empty_reply_reaches_the_caller_empty():
    """One retry only."""
    adapter = _bare_adapter()
    calls, fake_chat = _scripted(_budget_spent(), _budget_spent(), _answer())
    assert _chat(adapter, fake_chat) == ""
    assert len(calls) == 2


# --- streaming path ------------------------------------------------------

def _thinking_stream():
    """Chunks of a stream whose reasoning used the whole budget."""
    return iter([
        {"message": {"content": "", "thinking": "reasoning "}, "done": False},
        {"message": {"content": "", "thinking": "more "}, "done": False},
        {"message": {"content": ""}, "done": True, "done_reason": "length",
         "eval_count": OLLAMA_NUM_PREDICT},
    ])


def _answer_stream(*parts):
    chunks = [{"message": {"content": p}, "done": False} for p in parts]
    chunks.append({"message": {"content": ""}, "done": True,
                   "done_reason": "stop", "eval_count": 5})
    return iter(chunks)


def _stream(adapter, fake_chat, **kwargs):
    with patch("ollama.chat", side_effect=fake_chat), \
            patch.object(adapter, "_get_num_ctx", return_value=8192):
        return list(adapter._chat_ollama_stream("system", "user", **kwargs))


def test_spent_budget_stream_is_restreamed_without_thinking(caplog):
    adapter = _bare_adapter()
    calls, fake_chat = _scripted(_thinking_stream(), _answer_stream("A plain ", "answer."))
    with caplog.at_level("INFO", logger="ember.llm"):
        chunks = _stream(adapter, fake_chat)
    assert "".join(chunks) == "A plain answer."
    assert len(calls) == 2
    assert "think" not in calls[0]
    assert calls[1]["think"] is False and calls[1]["stream"] is True
    assert calls[1]["messages"] == calls[0]["messages"]
    assert "[GEN] reasoning used the whole output budget" in caplog.text


def test_stream_with_text_is_one_call():
    """Absence test; the test above is its positive control."""
    adapter = _bare_adapter()
    calls, fake_chat = _scripted(_answer_stream("A plain ", "answer."), _answer_stream("x"))
    assert "".join(_stream(adapter, fake_chat)) == "A plain answer."
    assert len(calls) == 1


def test_stream_retry_yields_the_prefix_once():
    adapter = _bare_adapter()
    calls, fake_chat = _scripted(_thinking_stream(), _answer_stream("the answer."))
    chunks = _stream(adapter, fake_chat, assistant_prefix="Based on current search results, ")
    assert "".join(chunks) == "Based on current search results, the answer."
    assert len(calls) == 2


def test_stream_already_without_thinking_is_not_restreamed():
    adapter = _bare_adapter()
    calls, fake_chat = _scripted(_thinking_stream(), _answer_stream("x"))
    assert _stream(adapter, fake_chat, think=False) == []
    assert len(calls) == 1


# --- through generate_response_iter --------------------------------------

def _turn(*replies):
    """Run one generate_response_iter turn on a scripted ollama.chat;
    review not triggered. Returns (final reply, calls)."""
    from unittest.mock import MagicMock

    from src.context.models import ContextPacket
    from src.safety.models import SafetyTriggerResult

    adapter = _bare_adapter()
    adapter.policy_service = MagicMock()
    adapter.policy_service.evaluate_trigger.return_value = SafetyTriggerResult(
        triggered=False, triggered_by=[])
    adapter._maybe_compress_buffer = MagicMock()
    buf = MagicMock(question_suppressed=False)
    packet = ContextPacket(user_message="That was a hard week.")
    calls, fake_chat = _scripted(*replies)
    with patch("ollama.chat", side_effect=fake_chat), \
            patch.object(adapter, "_get_num_ctx", return_value=8192), \
            patch.object(adapter, "_prepare_turn_prompt",
                         return_value=(buf, "system prompt", packet)):
        items = list(adapter.generate_response_iter(packet))
    return items[-1], calls


def test_turn_whose_reasoning_spent_the_budget_gets_a_reply():
    reply, calls = _turn(_budget_spent(), _answer("That sounds like a lot."))
    assert reply == "That sounds like a lot."
    assert len(calls) == 2


def test_turn_still_empty_after_the_retry_gets_the_fallback():
    """Control for the test above: it can tell the fallback from a reply."""
    reply, calls = _turn(_budget_spent(), _budget_spent())
    assert reply.startswith("I had trouble generating a response")
    assert len(calls) == 2
