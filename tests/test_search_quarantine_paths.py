"""
tests/test_search_quarantine_paths.py

The AI-doc quarantine (resolve_search_outcome, src/context/service.py)
applies to every search result before it enters the context packet: context
assembly, the ask-first confirm path (_apply_confirmation) and the autonomous
backstop (_complete_exchange). Search is mocked; the vault is the conftest
throwaway.

Section 4 covers the turn after a search that a retry cannot fix (reason
filtered or low_relevance): no ask-first pending_confirmation, and a search
note that tells the model not to offer again. Section 5 covers the confirm
retry offer against a merged packet and the tightened escape hatch.

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
from src.llm.ask_first_validator import RETRY_OFFER_RESPONSE
from src.llm.post_gen_pipeline import (
    WEB_SEARCH_UNAVAILABLE_DISCLOSURE,
    run_post_gen_pipeline,
)
from src.llm.prompt_builder import PromptBuilder
from src.tools.web_search import WebSearchResult
from tests.conftest import active_pendings as _active_pendings
from tests.conftest import seed_pending

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
# Matches the quarantine's escape hatch ("compare" plus an AI-system name):
# the user is asking about another AI system, so AI-doc hits pass through.
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
               return_value=pending), \
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


def _searxng_response(items, unresponsive=()):
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "results": [_searxng_hit(i) for i in items],
        "unresponsive_engines": [[name, "timeout"] for name in unresponsive],
    }
    response.raise_for_status.return_value = None
    return response


def _autonomous(client, items, *, query=_QUERY, stream=False):
    """One autonomous turn where context assembly does not search (its policy
    is patched to default) and the request classifier routes to web_search,
    so the backstop in _complete_exchange runs the search. Only SearXNG,
    retrieval and the model are stubbed."""
    resp, text, packet, _ = _turn(client, [_searxng_response(items)],
                                  query=query, stream=stream)
    return resp, text, packet


def _turn(client, responses, *, query=_QUERY, stream=False, autonomous=True,
          confirm=False, assembly_searches=False, min_term_hits=0,
          reply=_MODEL_REPLY, real_pending=None):
    """One /v1/chat/completions turn with SearXNG answering `responses` in
    order, one per search. confirm=True makes this the "yes" turn of an
    ask-first offer for `query`. assembly_searches=True lets context assembly
    search too (autonomous switched on while the offer was pending).
    real_pending=(test_vault, session_id) runs the REAL pending checker and
    writer against the throwaway test vault for that synthetic session
    instead of mocking them; the caller seeds the pending.
    Returns (resp, text, packet, _write_pending_confirmation mock or None)."""
    import src.api.openai_adapter as adapter
    from src.context.policies import ContextPolicy, _web_search_policy

    assembly_policy = (_web_search_policy(explicit=False) if assembly_searches
                       else ContextPolicy(name="default"))
    with ExitStack() as stack:
        get = stack.enter_context(
            patch.object(web_search_mod.requests, "get", side_effect=responses))
        stack.enter_context(patch.object(web_search_mod, "get_web_search_min_term_hits",
                                         return_value=min_term_hits))
        stack.enter_context(patch("src.context.service.classify_query",
                                  return_value=assembly_policy))
        stack.enter_context(patch("src.context.policies.classify_query",
                                  return_value=_web_search_policy(explicit=False)))
        stack.enter_context(patch.object(adapter.context_service.retriever, "retrieve",
                                         return_value=([], [], [], [], None)))
        llm = stack.enter_context(patch("src.api.openai_adapter.llm_adapter"))
        recorder = stack.enter_context(patch("src.api.openai_adapter.ExchangeRecorder"))
        for name in ("_background_state_extraction",
                     "_detect_and_write_commitment", "_detect_task_in_response",
                     "_background_topic_decline_resolution"):
            stack.enter_context(patch(f"src.api.openai_adapter.{name}"))
        onb = stack.enter_context(patch("src.api.openai_adapter.onboarding_service"))
        onb.is_active.return_value = False
        stack.enter_context(patch("src.core.preferences.get",
                                  side_effect=lambda k, d=None, *a, **kw:
                                  autonomous if k == "web_search_autonomous" else d))
        headers = {}
        if real_pending is not None:
            test_vault, session_id = real_pending
            # A real vault path for vault_binding and a JSON-serialisable
            # session id for the pending record.
            recorder.return_value.vault = str(test_vault)
            recorder.return_value.session_id = session_id
            stack.enter_context(patch("src.api.openai_adapter._spawn_deviation_detection"))
            headers = {"X-Session-ID": session_id}
            write_pending = None
        else:
            write_pending = stack.enter_context(
                patch("src.api.openai_adapter._write_pending_confirmation"))
            pending = {"confirmed": True, "action": "web_search", "query": query}
            stack.enter_context(patch(
                "src.api.openai_adapter._check_pending_confirmation",
                return_value=pending if confirm else None))
        stack.enter_context(patch("src.llm.coaching_filter.filter_coaching_frame",
                                  side_effect=lambda reply, *a, **kw: reply))
        stack.enter_context(patch("src.safety.grounding_check.should_short_circuit_grounding",
                                  return_value=False))
        stack.enter_context(patch("src.safety.grounding_check.run_grounding_check",
                                  new=AsyncMock(return_value=(True, None))))
        llm.generate_response.return_value = reply
        llm.generate_response_iter.return_value = iter([reply])

        resp = client.post("/v1/chat/completions", json={
            "model": "ember",
            "messages": [{"role": "user", "content": "yes" if confirm else query}],
            "stream": stream,
        }, headers=headers)
    assert resp.status_code == 200
    # Every scripted search ran (the backstop or the confirm path, plus
    # assembly when it searches) and none ran twice.
    assert get.call_count == len(responses)
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
    return resp, text, packet, write_pending


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


# --- 4. No repeat search offer after a filtered result -------------------------

_OFFER_REPLY = "I could not find that. Want me to search again?"
_JUNK_HIT = {
    "title": "Weather report",
    "url": "https://example.com/weather",
    "snippet": "sunny skies",
    "published_at": None,
}
_NO_REPEAT = "do not offer to search again"
# Hits run_web_search hands the quarantine: low_relevance drops them inside
# run_web_search, the quarantine drops a filtered hit after it.
_RETURNED = {"filtered": 1, "low_relevance": 0}


def _failing_search(reason):
    """(SearXNG response, min_term_hits) that make one search end in reason."""
    if reason == "filtered":
        return _searxng_response([_AI_DOC_HIT]), 0
    if reason == "low_relevance":
        # _JUNK_HIT shares none of _QUERY's three content terms.
        return _searxng_response([_JUNK_HIT]), 3
    assert reason == "engines_blocked"
    return _searxng_response([], unresponsive=["brave"]), 0


@pytest.mark.parametrize("reason", ["filtered", "low_relevance"])
def test_search_note_forbids_a_repeat_offer(reason):
    from src.llm.prompt_builder import render_search_note
    assert _NO_REPEAT in render_search_note("unavailable", reason)


def test_search_note_engines_blocked_keeps_the_offer_open():
    """Positive control for the note line above: a blocked search may work
    on retry, so its note does not forbid the offer."""
    from src.llm.prompt_builder import render_search_note
    note = render_search_note("unavailable", "engines_blocked")
    assert note and _NO_REPEAT not in note


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("reason", ["filtered", "low_relevance"])
def test_autonomous_no_pending_after_unrepeatable_failure(client, reason, stream, caplog):
    response, min_hits = _failing_search(reason)
    with caplog.at_level("INFO", logger="ember.openai_adapter"):
        resp, _, packet, write_pending = _turn(
            client, [response], min_term_hits=min_hits, reply=_OFFER_REPLY,
            stream=stream)
    assert resp.headers.get("x-ember-web-search") == "unavailable"
    if packet is not None:
        assert packet.web_search_reason == reason
        assert _NO_REPEAT in _search_section(packet)
    write_pending.assert_not_called()
    assert f"pending_confirmation not written: search reason={reason}" in caplog.text
    assert (f"path=autonomous: 0 of {_RETURNED[reason]} results kept, status=unavailable reason={reason}"
            in caplog.text)


@pytest.mark.parametrize("stream", [False, True])
def test_autonomous_engines_blocked_still_offers(client, stream):
    """Positive control for the test above: a blocked search can succeed on
    retry, so the model's offer reaches the pending writer."""
    response, _ = _failing_search("engines_blocked")
    _, _, packet, write_pending = _turn(client, [response], reply=_OFFER_REPLY,
                                        stream=stream)
    if packet is not None:
        assert packet.web_search_reason == "engines_blocked"
        assert _NO_REPEAT not in _search_section(packet)
    write_pending.assert_called_once()
    assert write_pending.call_args.args[0].endswith("Want me to search again?")


@pytest.mark.parametrize("reason", ["filtered", "low_relevance"])
def test_confirm_no_pending_after_unrepeatable_failure(client, reason, caplog):
    """No canned retry offer either: the model's reply stands, behind the
    disclosure line."""
    response, min_hits = _failing_search(reason)
    with caplog.at_level("INFO", logger="ember.openai_adapter"):
        _, text, packet, write_pending = _turn(
            client, [response], autonomous=False, confirm=True,
            min_term_hits=min_hits, reply=_OFFER_REPLY)
    assert packet.web_search_reason == reason
    assert _NO_REPEAT in _search_section(packet)
    assert text == f"{WEB_SEARCH_UNAVAILABLE_DISCLOSURE}\n\n{_OFFER_REPLY}"
    write_pending.assert_not_called()
    assert (f"path=confirm: 0 of {_RETURNED[reason]} results kept, status=unavailable reason={reason}"
            in caplog.text)


def test_confirm_engines_blocked_still_gets_the_retry_offer(client, isolate_to_test_vault):
    """Positive control for the test above: a blocked confirm search routes
    to the post-gen retry offer (engines_blocked is not in
    NO_REPEAT_OFFER_REASONS), and that offer leaves a REAL active pending for
    the original query in the throwaway test vault. The user's next "yes"
    re-runs the search."""
    session_id = "sess_test_confirm_retry_pending"
    seeded = seed_pending(session_id, _QUERY)
    real = (isolate_to_test_vault, session_id)

    response, _ = _failing_search("engines_blocked")
    _, text, _, _ = _turn(client, [response], autonomous=False, confirm=True,
                          reply=_OFFER_REPLY, real_pending=real)
    assert text == RETRY_OFFER_RESPONSE
    pendings = _active_pendings(session_id)
    assert [p.metadata["query"] for p in pendings] == [_QUERY]
    assert seeded.id not in [p.id for p in pendings]

    resp, text, packet, _ = _turn(client, [_searxng_response([_CLEAN_HIT])],
                                  autonomous=False, confirm=True, real_pending=real)
    assert [i["url"] for i in packet.web_items] == [_CLEAN_HIT["url"]]
    assert text == _MODEL_REPLY
    assert resp.headers.get("x-ember-web-search") == "true"
    assert _active_pendings(session_id) == []


def test_autonomous_empty_search_logs_its_path(client, caplog):
    with caplog.at_level("INFO", logger="ember.openai_adapter"):
        _turn(client, [_searxng_response([])])
    assert ("path=autonomous: 0 of 0 results kept, status=empty reason=no_results"
            in caplog.text)


# --- 5. Review follow-ups -----------------------------------------------------

@pytest.mark.parametrize("assembly_items,expect_retry", [
    ([_CLEAN_HIT], False),
    ([], True),
])
def test_confirm_failure_with_merged_items_is_not_a_retry(client, assembly_items, expect_retry):
    """The confirm search is blocked and context assembly searches the same
    query (autonomous switched on while the offer was pending). Good items
    from assembly answer the turn; the retry offer fires only when the merged
    packet has none (the second case is the control)."""
    blocked, _ = _failing_search("engines_blocked")
    second = (_searxng_response(assembly_items) if assembly_items
              else _searxng_response([], unresponsive=["brave"]))
    resp, text, packet, _ = _turn(
        client, [blocked, second], confirm=True, assembly_searches=True)
    if expect_retry:
        assert text == RETRY_OFFER_RESPONSE
    else:
        assert [i["url"] for i in packet.web_items] == [_CLEAN_HIT["url"]]
        assert text == _MODEL_REPLY
        assert resp.headers.get("x-ember-web-search") == "true"


def test_bare_compare_does_not_open_the_escape_hatch():
    from src.context.service import _quarantine_ai_docs
    safe, quarantined = _quarantine_ai_docs([_AI_DOC_HIT], "compare laptop prices")
    assert (safe, quarantined) == ([], [_AI_DOC_HIT])


def test_compare_with_ai_names_opens_the_escape_hatch():
    """Positive control for the test above."""
    from src.context.service import _quarantine_ai_docs
    safe, quarantined = _quarantine_ai_docs([_AI_DOC_HIT], "compare Claude and GPT")
    assert (safe, quarantined) == ([_AI_DOC_HIT], [])


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
