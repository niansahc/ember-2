"""
tests/test_single_classification.py

One intent classification per chat turn. The adapter classifies the turn
(gen_ctx.policy) and passes that policy to context_service.build_context.
Before the fix it passed policy=None, so build_context ran classify_query
again: a second ADR-034 cascade, and a second Stage 3 model call whose
verdict could differ from the one the adapter acted on.

classify_intent is replaced by a counter, so no model is involved. The
turn harness follows tests/test_ask_first_routing.py::_turn, minus the
classify_query patches (they would hide the second classification).
Synthetic session ids only.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

import src.tools.web_search as web_search_mod

QUERY = "what should I cook tonight"


class _CountingClassifier:
    """Stands in for src.context.policies.classify_intent."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, query: str) -> str:
        self.calls += 1
        return "vault_answerable"


@pytest.fixture
def counter():
    fake = _CountingClassifier()
    with patch("src.context.policies.classify_intent", fake):
        yield fake


def _turn(client, test_vault, session_id: str) -> None:
    import src.api.openai_adapter as adapter

    with ExitStack() as stack:
        stack.enter_context(patch.object(web_search_mod.requests, "get"))
        stack.enter_context(patch.object(adapter.context_service.retriever, "retrieve",
                                         return_value=([], [], [], [], None)))
        llm = stack.enter_context(patch("src.api.openai_adapter.llm_adapter"))
        rec = stack.enter_context(patch("src.api.openai_adapter.ExchangeRecorder"))
        rec.return_value.vault = str(test_vault)
        rec.return_value.session_id = session_id
        for name in ("_background_state_extraction", "_detect_and_write_commitment",
                     "_detect_task_in_response", "_background_topic_decline_resolution",
                     "_spawn_deviation_detection", "_spawn_post_exchange_extractors"):
            stack.enter_context(patch(f"src.api.openai_adapter.{name}"))
        onb = stack.enter_context(patch("src.api.openai_adapter.onboarding_service"))
        onb.is_active.return_value = False
        stack.enter_context(patch("src.core.preferences.get",
                                  side_effect=lambda k, d=None, *a, **kw: d))
        stack.enter_context(patch(
            "src.api.openai_adapter._prior_turn_awaits_search_content",
            return_value=False))
        stack.enter_context(patch("src.llm.coaching_filter.filter_coaching_frame",
                                  side_effect=lambda reply, *a, **kw: reply))
        stack.enter_context(patch("src.safety.grounding_check.should_short_circuit_grounding",
                                  return_value=False))
        stack.enter_context(patch("src.safety.grounding_check.run_grounding_check",
                                  new=AsyncMock(return_value=(True, None))))
        llm.generate_response.return_value = "Here is an answer."
        resp = client.post(
            "/v1/chat/completions",
            json={"model": "ember", "stream": False,
                  "messages": [{"role": "user", "content": QUERY}]},
            headers={"X-Session-ID": session_id},
        )
    assert resp.status_code == 200


def test_a_chat_turn_classifies_intent_once(keyless_client, isolate_to_test_vault, counter):
    _turn(keyless_client, isolate_to_test_vault, "sess_test_single_classify")
    assert counter.calls == 1


def test_build_context_without_a_policy_classifies(counter):
    """Positive control: the counter sees an assembly-side classification.
    If the adapter went back to policy=None, the turn above would read 2."""
    import src.api.openai_adapter as adapter

    with patch.object(adapter.context_service.retriever, "retrieve",
                      return_value=([], [], [], [], None)):
        adapter.context_service.build_context(QUERY, skip_web_search=True,
                                              read_only=True)
    assert counter.calls == 1


def test_build_context_with_a_policy_does_not_classify(counter):
    import src.api.openai_adapter as adapter
    from src.context.policies import ContextPolicy

    with patch.object(adapter.context_service.retriever, "retrieve",
                      return_value=([], [], [], [], None)):
        adapter.context_service.build_context(QUERY, skip_web_search=True,
                                              read_only=True,
                                              policy=ContextPolicy(name="default"))
    assert counter.calls == 0
