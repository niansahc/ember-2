"""
tests/test_web_search_status.py

run_web_search reports why a search came back with nothing, so the turn can
tell the user search was unavailable instead of answering as if it had
searched. SearXNG returns HTTP 200 with an empty result list when every
engine is blocked upstream; the only signal is `unresponsive_engines`.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests
from fastapi.testclient import TestClient

import src.tools.web_search as web_search_mod
from src.llm.post_gen_pipeline import WEB_SEARCH_UNAVAILABLE_DISCLOSURE
from src.tools.web_search import WebSearchResult, run_web_search, web_search


def _searxng(payload):
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return patch.object(web_search_mod.requests, "get", return_value=response)


_ONE_RESULT = {
    "results": [{"title": "T", "url": "https://example.com/a", "content": "snippet"}],
    "unresponsive_engines": [],
}


def test_results_are_ok():
    with _searxng(_ONE_RESULT):
        result = run_web_search("synthetic query")
    assert result.status == "ok"
    assert [i["url"] for i in result.items] == ["https://example.com/a"]


def test_no_results_with_every_engine_answering_is_empty():
    with _searxng({"results": [], "unresponsive_engines": []}):
        result = run_web_search("synthetic query")
    assert (result.status, result.items, result.unresponsive) == ("empty", [], [])


def test_no_results_with_blocked_engines_is_unavailable(caplog):
    payload = {
        "results": [],
        "unresponsive_engines": [
            ["brave", "too many requests"],
            ["duckduckgo", "CAPTCHA"],
        ],
    }
    with _searxng(payload), caplog.at_level("WARNING", logger="ember.web_search"):
        result = run_web_search("synthetic query")
    assert result.status == "unavailable"
    assert result.items == []
    assert result.unresponsive == ["brave", "duckduckgo"]
    assert "brave" in caplog.text and "duckduckgo" in caplog.text


def test_results_with_some_blocked_engines_are_still_ok():
    payload = dict(_ONE_RESULT, unresponsive_engines=[["startpage", "CAPTCHA"]])
    with _searxng(payload):
        result = run_web_search("synthetic query")
    assert result.status == "ok"
    assert result.unresponsive == ["startpage"]


def test_request_failure_is_unavailable():
    with patch.object(web_search_mod.requests, "get",
                      side_effect=requests.ConnectionError):
        result = run_web_search("synthetic query")
    assert (result.status, result.items) == ("unavailable", [])


def test_web_search_wrapper_still_returns_items_only():
    with _searxng({"results": [], "unresponsive_engines": [["brave", "x"]]}):
        assert web_search("synthetic query") == []
    with _searxng(_ONE_RESULT):
        assert [i["url"] for i in web_search("synthetic query")] == ["https://example.com/a"]


# --- ContextPacket.web_search_status (context assembly) -----------------------

def _build_packet(search_result, *, skip_web_search=False):
    from src.context.policies import _web_search_policy
    from src.context.service import ContextService

    service = ContextService()
    with patch("src.context.service.classify_query",
               return_value=_web_search_policy(explicit=False)), \
         patch.object(service.retriever, "retrieve",
                      return_value=([], [], [], [], None)), \
         patch("src.context.service.run_web_search",
               return_value=search_result) as search:
        packet = service.build_context("synthetic news query",
                                       skip_web_search=skip_web_search,
                                       read_only=True)
    return packet, search


def test_packet_records_unavailable_search():
    packet, search = _build_packet(WebSearchResult(status="unavailable",
                                                   unresponsive=["brave"]))
    search.assert_called_once()
    assert packet.web_items == []
    assert packet.web_search_status == "unavailable"


def test_packet_records_ok_search_with_items():
    item = {"title": "T", "url": "https://example.com/a", "snippet": "s",
            "published_at": None}
    packet, _ = _build_packet(WebSearchResult(items=[item], status="ok"))
    assert packet.web_search_status == "ok"
    assert [i["url"] for i in packet.web_items] == ["https://example.com/a"]


def test_packet_status_is_none_when_search_skipped():
    """None means not attempted. The two tests above are the positive
    control: the same fixture does record a status when search runs."""
    packet, search = _build_packet(WebSearchResult(status="unavailable"),
                                   skip_web_search=True)
    search.assert_not_called()
    assert packet.web_search_status is None


# --- Chat endpoint: one search, disclosure line, header ----------------------

_DISCLOSURE = (
    "Web search isn't working right now, so this answer is from what I "
    "already know and may be out of date."
)
_QUERY = "what's the latest news about AI"
_MODEL_REPLY = "Here is a general answer from training data."
_BLOCKED = {"results": [], "unresponsive_engines": [["brave", "too many requests"],
                                                    ["duckduckgo", "CAPTCHA"],
                                                    ["startpage", "CAPTCHA"]]}


@pytest.fixture
def client():
    with patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        yield TestClient(app)


def _chat(client, searxng_payload, *, stream, model_reply=_MODEL_REPLY):
    """Drive one autonomous web_search turn through the real context service
    and the real web_search module; only SearXNG, retrieval and the model are
    stubbed."""
    import src.api.openai_adapter as adapter

    response = MagicMock(status_code=200)
    response.json.return_value = searxng_payload
    response.raise_for_status.return_value = None
    with ExitStack() as stack:
        get = stack.enter_context(
            patch.object(web_search_mod.requests, "get", return_value=response))
        stack.enter_context(patch.object(adapter.context_service.retriever, "retrieve",
                                         return_value=([], [], [], [], None)))
        llm = stack.enter_context(patch("src.api.openai_adapter.llm_adapter"))
        for name in ("ExchangeRecorder", "_background_state_extraction",
                     "_detect_and_write_commitment", "_detect_task_in_response",
                     "_background_topic_decline_resolution"):
            stack.enter_context(patch(f"src.api.openai_adapter.{name}"))
        onb = stack.enter_context(patch("src.api.openai_adapter.onboarding_service"))
        onb.is_active.return_value = False
        stack.enter_context(patch("src.core.preferences.get",
                                  side_effect=lambda k, d=None, *a, **kw:
                                  True if k == "web_search_autonomous" else d))
        # Grounding check, revision and the coaching filter's semantic check
        # reach Ollama; pass the reply through.
        stack.enter_context(patch("src.llm.coaching_filter.filter_coaching_frame",
                                  side_effect=lambda reply, *a, **kw: reply))
        stack.enter_context(patch("src.safety.grounding_check.should_short_circuit_grounding",
                                  return_value=False))
        stack.enter_context(patch("src.safety.grounding_check.run_grounding_check",
                                  new=AsyncMock(return_value=(True, None))))
        llm.generate_response.return_value = model_reply
        llm.generate_response_iter.return_value = iter([model_reply])

        resp = client.post("/v1/chat/completions", json={
            "model": "ember",
            "messages": [{"role": "user", "content": _QUERY}],
            "stream": stream,
        })
    assert resp.status_code == 200
    if stream:
        text = "".join(
            json.loads(line[6:])["choices"][0]["delta"].get("content") or ""
            for line in resp.text.splitlines()
            if line.startswith("data: {") and '"choices"' in line
        )
    else:
        text = resp.json()["choices"][0]["message"]["content"]
    return resp, text, get


def test_disclosure_text_is_exact():
    assert WEB_SEARCH_UNAVAILABLE_DISCLOSURE == _DISCLOSURE


@pytest.mark.parametrize("stream", [False, True])
def test_blocked_search_discloses_and_searches_once(client, stream):
    resp, text, get = _chat(client, _BLOCKED, stream=stream)
    assert get.call_count == 1
    assert text.startswith(_DISCLOSURE)
    assert _MODEL_REPLY in text
    assert resp.headers.get("x-ember-web-search") == "unavailable"


@pytest.mark.parametrize("stream", [False, True])
def test_working_search_has_no_disclosure(client, stream):
    """Positive control for the disclosure: same turn, results present."""
    resp, text, get = _chat(client, _ONE_RESULT, stream=stream)
    assert get.call_count == 1
    assert _DISCLOSURE not in text
    assert resp.headers.get("x-ember-web-search") == "true"


def test_empty_search_has_header_but_no_disclosure(client):
    resp, text, _ = _chat(client, {"results": [], "unresponsive_engines": []},
                          stream=False)
    assert _DISCLOSURE not in text
    assert resp.headers.get("x-ember-web-search") == "empty"


def test_refusal_reply_gets_no_disclosure(client):
    resp, text, _ = _chat(client, _BLOCKED, stream=False,
                          model_reply="I can't help with that.")
    assert _DISCLOSURE not in text
    assert resp.headers.get("x-ember-web-search") == "unavailable"


# --- Ask-first confirm path ---------------------------------------------------

def _confirm(search_result):
    from src.api.openai_adapter import _apply_confirmation
    from src.api.pregeneration import GenerationContext, GenerationWork

    ctx = GenerationContext(
        session_id="sess_test_confirm", project_id=None, project_name=None,
        is_test=False, vault_enabled=True, skip_vault_read=False,
        skip_vault_write=False, completion_id="chatcmpl-confirm", stream=False,
        policy=None, raw_user_message="",
    )
    work = GenerationWork(message="yes")
    pending = {"confirmed": True, "action": "web_search", "query": "synthetic query"}
    with patch("src.api.openai_adapter._check_pending_confirmation",
               return_value=(pending, [])), \
         patch("src.tools.web_search.run_web_search", return_value=search_result):
        _apply_confirmation(ctx, work)
    return work


def test_confirmed_search_unavailable_routes_to_retry_offer():
    from src.llm.post_gen_pipeline import run_post_gen_pipeline

    work = _confirm(WebSearchResult(status="unavailable", unresponsive=["brave"]))
    assert work.confirmation_search_failed is True
    assert work.confirmation_search_status == "unavailable"

    result = run_post_gen_pipeline(
        "A model reply.", intent_class="web_search", web_search_autonomous=False,
        used_web_search=False, used_vault=False, used_vision=False,
        confirmation_search_failed=work.confirmation_search_failed,
        web_search_status=work.confirmation_search_status,
    )
    # The retry offer replaces the reply; no disclosure line on top of it.
    assert result.reply == "I tried searching but hit an error. Want me to try again?"
    assert result.search_unavailable_disclosed is False


def test_confirmed_search_with_results_is_not_a_failure():
    """Positive control: the same confirm fixture with results does not
    flag a failure."""
    item = {"title": "T", "url": "https://example.com/a", "snippet": "s",
            "published_at": None}
    work = _confirm(WebSearchResult(items=[item], status="ok"))
    assert work.confirmation_search_failed is False
    assert work.confirmation_web_items == [item]


# --- Post-gen disclosure, unit level ------------------------------------------

def _postgen(reply, status, **kw):
    from src.llm.post_gen_pipeline import run_post_gen_pipeline
    return run_post_gen_pipeline(
        reply, intent_class="web_search", web_search_autonomous=True,
        used_web_search=False, used_vault=False, used_vision=False,
        web_search_status=status, **kw,
    )


def test_postgen_discloses_only_when_unavailable():
    assert _postgen("Answer.", "unavailable").reply == f"{_DISCLOSURE}\n\nAnswer."
    for status in (None, "empty", "ok"):
        assert _postgen("Answer.", status).reply == "Answer."


def test_postgen_skips_disclosure_on_refusal_and_empty_fallback():
    assert _postgen("No.", "unavailable", is_refusal=True).reply == "No."
    fallback = _postgen("", "unavailable")
    assert fallback.empty_fallback_fired and _DISCLOSURE not in fallback.reply
