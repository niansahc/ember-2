"""
tests/test_search_quarantine_paths.py

The AI-doc quarantine (resolve_search_outcome, src/context/service.py)
applies to every search result before it enters the context packet: context
assembly, the ask-first confirm path (_apply_confirmation) and the autonomous
backstop (_complete_exchange). Search is mocked; the vault is the conftest
throwaway.

Each "AI hit absent" assertion has a positive control: the escape-hatch query
on the same fixture lets the AI hit through, so the fixture can reach the
packet. The structural test at the bottom keeps a future run_web_search caller
from skipping the quarantine.
"""

from __future__ import annotations

import ast
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import src.tools.web_search as web_search_mod
from src.context.models import ContextPacket
from src.llm.post_gen_pipeline import (
    WEB_SEARCH_UNAVAILABLE_DISCLOSURE,
    run_post_gen_pipeline,
)
from src.llm.prompt_builder import PromptBuilder
from src.tools.web_search import WebSearchResult

# The AI hit carries the query terms so a WEB_SEARCH_MIN_TERM_HITS setting
# cannot drop it before the quarantine sees it; "model card" quarantines it.
_AI_DOC_HIT = {
    "title": "Bitcoin price model card",
    "url": "https://example.com/card",
    "snippet": "model card for claude and gpt, bitcoin price",
    "published_at": None,
}
_CLEAN_HIT = {
    "title": "Bitcoin price",
    "url": "https://example.com/btc",
    "snippet": "bitcoin price today",
    "published_at": None,
}
_QUERY = "latest bitcoin price"
# Matches the quarantine's escape hatch ("compare"): the user is asking about
# another AI system, so AI-doc hits are allowed through.
_AI_QUERY = "compare bitcoin price claude and gpt"


def _search_section(packet) -> str:
    return PromptBuilder.__new__(PromptBuilder)._build_web_search_section(packet)


# --- 1. Context assembly ------------------------------------------------------

def _assemble(items, query=_QUERY):
    from src.context.policies import _web_search_policy
    from src.context.service import ContextService

    service = ContextService()
    with patch("src.context.service.classify_query",
               return_value=_web_search_policy(explicit=False)), \
         patch.object(service.retriever, "retrieve",
                      return_value=([], [], [], [], None)), \
         patch("src.context.service.run_web_search",
               return_value=WebSearchResult(items=list(items), status="ok")):
        return service.build_context(query, read_only=True)


def test_assembly_removes_quarantined_hit():
    packet = _assemble([_AI_DOC_HIT, _CLEAN_HIT])
    assert packet.web_items == [_CLEAN_HIT]
    assert (packet.web_search_status, packet.web_search_reason) == ("ok", None)


def test_assembly_escape_hatch_keeps_ai_hit():
    """Positive control for the removal above."""
    packet = _assemble([_AI_DOC_HIT, _CLEAN_HIT], query=_AI_QUERY)
    assert _AI_DOC_HIT in packet.web_items


def test_assembly_all_quarantined_is_filtered():
    packet = _assemble([_AI_DOC_HIT])
    assert packet.web_items == []
    assert (packet.web_search_status, packet.web_search_reason) == (
        "unavailable", "filtered")
    assert "reason: filtered" in _search_section(packet)


def test_assembly_clean_hit_passes():
    packet = _assemble([_CLEAN_HIT])
    assert packet.web_items == [_CLEAN_HIT]
    assert packet.web_search_status == "ok"


# --- 2. Ask-first confirm path ------------------------------------------------

def _confirm(items, query=_QUERY):
    """Run the confirm path on a mocked search and merge it into an empty
    packet, as _complete_exchange does."""
    from src.api.openai_adapter import _apply_confirmation, _merge_confirmation_search
    from src.api.pregeneration import GenerationContext, GenerationWork

    ctx = GenerationContext(
        session_id="sess_test_quarantine", project_id=None, project_name=None,
        is_test=False, vault_enabled=True, skip_vault_read=False,
        skip_vault_write=False, completion_id="chatcmpl-quarantine",
        stream=False, policy=None, raw_user_message="",
    )
    work = GenerationWork(message="yes")
    pending = {"confirmed": True, "action": "web_search", "query": query}
    with patch("src.api.openai_adapter._check_pending_confirmation",
               return_value=(pending, [])), \
         patch("src.tools.web_search.run_web_search",
               return_value=WebSearchResult(items=list(items), status="ok")):
        _apply_confirmation(ctx, work)
    packet = ContextPacket(user_message=query)
    _merge_confirmation_search(
        packet, work.confirmation_web_items,
        work.confirmation_search_status, work.confirmation_search_reason,
    )
    return work, packet


def _confirm_postgen(work, packet):
    return run_post_gen_pipeline(
        "A model reply.", intent_class="web_search", web_search_autonomous=False,
        used_web_search=bool(packet.web_items), used_vault=False,
        used_vision=False,
        confirmation_search_failed=work.confirmation_search_failed,
        web_search_status=packet.web_search_status,
    )


def test_confirm_removes_quarantined_hit():
    _, packet = _confirm([_AI_DOC_HIT, _CLEAN_HIT])
    assert packet.web_items == [_CLEAN_HIT]
    assert packet.web_search_status == "ok"


def test_confirm_escape_hatch_keeps_ai_hit():
    """Positive control for the removal above."""
    _, packet = _confirm([_AI_DOC_HIT, _CLEAN_HIT], query=_AI_QUERY)
    assert _AI_DOC_HIT in packet.web_items


def test_confirm_all_quarantined_is_filtered_without_retry_offer():
    """Option B: a filtered confirm search is not a retryable failure. The
    model answers with the search note and the disclosure line. Control for
    "no retry offer": test_web_search_status.py
    test_confirmed_search_unavailable_routes_to_retry_offer (engines blocked
    still routes to the retry offer)."""
    work, packet = _confirm([_AI_DOC_HIT])
    assert packet.web_items == []
    assert (packet.web_search_status, packet.web_search_reason) == (
        "unavailable", "filtered")
    assert work.confirmation_search_failed is False
    assert "reason: filtered" in _search_section(packet)

    result = _confirm_postgen(work, packet)
    assert result.reply == f"{WEB_SEARCH_UNAVAILABLE_DISCLOSURE}\n\nA model reply."


def test_confirm_clean_hit_passes():
    work, packet = _confirm([_CLEAN_HIT])
    assert packet.web_items == [_CLEAN_HIT]
    assert packet.web_search_status == "ok"
    assert work.confirmation_search_failed is False
    assert _confirm_postgen(work, packet).reply == "A model reply."


# --- 3. Autonomous backstop ---------------------------------------------------

_MODEL_REPLY = "Here is an answer."


def _searxng_hit(item):
    return {"title": item["title"], "url": item["url"], "content": item["snippet"]}


@pytest.fixture
def client():
    with patch("src.api.main.get_ember_api_key", return_value=None):
        from src.api.main import app
        yield TestClient(app)


def _autonomous(client, items, *, query=_QUERY, stream=False):
    """One autonomous turn where context assembly does not search (its policy
    is patched to default) and the request classifier routes to web_search,
    so the backstop in _complete_exchange runs the search. Only SearXNG,
    retrieval and the model are stubbed."""
    import src.api.openai_adapter as adapter
    from src.context.policies import ContextPolicy, _web_search_policy

    response = MagicMock(status_code=200)
    response.json.return_value = {
        "results": [_searxng_hit(i) for i in items], "unresponsive_engines": [],
    }
    response.raise_for_status.return_value = None

    with ExitStack() as stack:
        get = stack.enter_context(
            patch.object(web_search_mod.requests, "get", return_value=response))
        stack.enter_context(patch("src.context.service.classify_query",
                                  return_value=ContextPolicy(name="default")))
        stack.enter_context(patch("src.context.policies.classify_query",
                                  return_value=_web_search_policy(explicit=False)))
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
        stack.enter_context(patch("src.llm.coaching_filter.filter_coaching_frame",
                                  side_effect=lambda reply, *a, **kw: reply))
        stack.enter_context(patch("src.safety.grounding_check.should_short_circuit_grounding",
                                  return_value=False))
        stack.enter_context(patch("src.safety.grounding_check.run_grounding_check",
                                  new=AsyncMock(return_value=(True, None))))
        llm.generate_response.return_value = _MODEL_REPLY
        llm.generate_response_iter.return_value = iter([_MODEL_REPLY])

        resp = client.post("/v1/chat/completions", json={
            "model": "ember",
            "messages": [{"role": "user", "content": query}],
            "stream": stream,
        })
    assert resp.status_code == 200
    # The backstop ran the search (assembly did not).
    assert get.call_count == 1
    if stream:
        from tests.conftest import sse_events
        text = "".join(
            e["choices"][0]["delta"].get("content") or ""
            for e in sse_events(resp.text)
            if isinstance(e, dict) and e.get("choices")
        )
        packet = None
    else:
        text = resp.json()["choices"][0]["message"]["content"]
        packet = llm.generate_response.call_args.args[0]
    return resp, text, packet


def test_autonomous_removes_quarantined_hit(client):
    _, text, packet = _autonomous(client, [_AI_DOC_HIT, _CLEAN_HIT])
    assert [i["url"] for i in packet.web_items] == [_CLEAN_HIT["url"]]
    assert packet.web_search_status == "ok"


def test_autonomous_escape_hatch_keeps_ai_hit(client):
    """Positive control for the removal above."""
    _, _, packet = _autonomous(client, [_AI_DOC_HIT, _CLEAN_HIT], query=_AI_QUERY)
    assert _AI_DOC_HIT["url"] in [i["url"] for i in packet.web_items]


@pytest.mark.parametrize("stream", [False, True])
def test_autonomous_all_quarantined_is_filtered(client, stream):
    resp, text, packet = _autonomous(client, [_AI_DOC_HIT], stream=stream)
    assert text.startswith(WEB_SEARCH_UNAVAILABLE_DISCLOSURE)
    assert resp.headers.get("x-ember-web-search") == "unavailable"
    if packet is not None:
        assert packet.web_items == []
        assert (packet.web_search_status, packet.web_search_reason) == (
            "unavailable", "filtered")
        assert "reason: filtered" in _search_section(packet)


@pytest.mark.parametrize("stream", [False, True])
def test_autonomous_clean_hit_passes(client, stream):
    """Control for the disclosure above: same turn, clean hit."""
    resp, text, _ = _autonomous(client, [_CLEAN_HIT], stream=stream)
    assert WEB_SEARCH_UNAVAILABLE_DISCLOSURE not in text
    assert resp.headers.get("x-ember-web-search") == "true"


# --- Structural guard: every run_web_search caller quarantines ----------------

_SRC = Path(__file__).resolve().parent.parent / "src"
_SEARCH_MODULE = _SRC / "tools" / "web_search.py"


def _called_names(scope: ast.AST) -> set[str]:
    """Names called directly in scope, not in functions or classes nested in it."""
    names: set[str] = set()
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
        stack.extend(ast.iter_child_nodes(node))
    return names


def _search_callers(source: str) -> tuple[set[str], set[str]]:
    """(scopes that call run_web_search, those of them that skip
    resolve_search_outcome). The module body counts as scope "<module>"."""
    tree = ast.parse(source)
    scopes = [("<module>", tree)] + [
        (node.name, node) for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    callers: set[str] = set()
    violations: set[str] = set()
    for name, scope in scopes:
        called = _called_names(scope)
        if "run_web_search" in called:
            callers.add(name)
            if "resolve_search_outcome" not in called:
                violations.add(name)
    return callers, violations


def test_every_run_web_search_caller_applies_the_quarantine():
    callers: set[str] = set()
    violations: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if path == _SEARCH_MODULE:
            continue
        found, bad = _search_callers(path.read_text(encoding="utf-8"))
        callers |= found
        violations += [f"{path.relative_to(_SRC.parent)}:{name}" for name in sorted(bad)]
    # The scan reached the three known sites; an empty scan would pass vacuously.
    assert {"_build_context", "_apply_confirmation", "_complete_exchange"} <= callers
    assert violations == []


def test_structural_checker_flags_a_caller_that_skips_the_quarantine():
    """Positive control for the guard above."""
    skips = (
        "def handler(q):\n"
        "    from src.tools.web_search import run_web_search\n"
        "    return run_web_search(q).items\n"
    )
    assert _search_callers(skips) == ({"handler"}, {"handler"})

    applies = (
        "def handler(q):\n"
        "    return resolve_search_outcome(web_search.run_web_search(q), q)\n"
    )
    assert _search_callers(applies) == ({"handler"}, set())
