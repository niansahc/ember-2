"""
tests/test_search_failure_status.py

Search failure -> search note (model-facing), junk-hit suppression, and the
thinking-off arm. Everything is mocked: no network, no SearXNG, nothing on
disk. See CONTEXT.md ("Web search") for the terms used here.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

import src.tools.web_search as web_search_mod
from src.context.models import ContextPacket
from src.context.service import resolve_search_outcome
from src.llm.adapter import LLMAdapter, search_failure_think
from src.llm.prompt_builder import PromptBuilder
from src.llm.post_gen_pipeline import (
    WEB_SEARCH_UNAVAILABLE_DISCLOSURE,
    run_post_gen_pipeline,
)
from src.tools.web_search import WebSearchResult, run_web_search


def _searxng(payload):
    response = MagicMock(status_code=200)
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return patch.object(web_search_mod.requests, "get", return_value=response)


def _hit(title="T", url="https://example.com/a", content="snippet"):
    return {"title": title, "url": url, "content": content}


def _builder():
    return PromptBuilder.__new__(PromptBuilder)


def _section(status, reason=None, items=None):
    packet = ContextPacket(
        user_message="q",
        web_items=items or [],
        web_search_status=status,
        web_search_reason=reason,
    )
    return _builder()._build_web_search_section(packet)


# ---------------------------------------------------------------------------
# 1. Each failure type carries a reason and renders the search note
# ---------------------------------------------------------------------------

def test_blocked_engines_reason():
    payload = {"results": [], "unresponsive_engines": [["brave", "too many requests"]]}
    with _searxng(payload):
        result = run_web_search("synthetic query")
    assert (result.status, result.reason) == ("unavailable", "engines_blocked")


def test_timeout_reason():
    with patch.object(web_search_mod.requests, "get",
                      side_effect=requests.Timeout):
        result = run_web_search("synthetic query")
    assert (result.status, result.reason) == ("unavailable", "timeout")


def test_connection_error_reason():
    with patch.object(web_search_mod.requests, "get",
                      side_effect=requests.ConnectionError):
        result = run_web_search("synthetic query")
    assert (result.status, result.reason) == ("unavailable", "request_error")


def test_http_error_reason():
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError("503")
    with patch.object(web_search_mod.requests, "get", return_value=response):
        result = run_web_search("synthetic query")
    assert (result.status, result.reason) == ("unavailable", "request_error")


def test_malformed_json_reason():
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("not json")
    with patch.object(web_search_mod.requests, "get", return_value=response):
        result = run_web_search("synthetic query")
    assert (result.status, result.reason) == ("unavailable", "request_error")


def test_empty_reason():
    with _searxng({"results": [], "unresponsive_engines": []}):
        result = run_web_search("synthetic query")
    assert (result.status, result.reason) == ("empty", "no_results")


def test_success_has_no_reason():
    with _searxng({"results": [_hit()], "unresponsive_engines": []}):
        result = run_web_search("synthetic query")
    assert (result.status, result.reason) == ("ok", None)


@pytest.mark.parametrize("reason", [
    "engines_blocked", "timeout", "request_error", "low_relevance", "filtered",
])
def test_unavailable_note_is_typed(reason):
    section = _section("unavailable", reason)
    assert section.startswith("<web_search_status>\n")
    assert section.endswith("</web_search_status>")
    assert "status: unavailable\n" in section
    assert f"reason: {reason}\n" in section
    assert "retrieved_content: none\n" in section
    assert "No content was retrieved from the web for this turn." in section
    assert "Do not state specific recent facts." in section
    assert "do not say that you cannot search" in section


def test_empty_note_is_typed_and_says_nothing_found():
    section = _section("empty", "no_results")
    assert "status: empty\n" in section
    assert "reason: no_results\n" in section
    assert "retrieved_content: none\n" in section
    assert "The search ran and found nothing. Say that nothing was found." in section
    assert "do not say that you cannot search" not in section


def test_note_never_leaks_urls_engines_or_errors():
    section = _section("unavailable", "engines_blocked")
    for leaked in ("http", "brave", "Timeout", "ConnectionError", "Traceback"):
        assert leaked not in section


def test_leak_check_has_a_positive_control():
    """A hit section does carry a URL, so the leak assertion above can fail."""
    section = _section(
        "ok", None,
        items=[{"title": "T", "url": "https://example.com/a",
                "snippet": "s", "published_at": None}],
    )
    assert "http" in section


def test_old_notice_tag_is_gone():
    assert "<web_search_results" not in _section("unavailable", "timeout")


# ---------------------------------------------------------------------------
# 2. Quarantine emptying the list is a failure, not a blank section
# ---------------------------------------------------------------------------

_AI_DOC_HIT = {
    "title": "Claude and GPT model card",
    "url": "https://example.com/card",
    "snippet": "model card for claude and gpt",
    "published_at": None,
}


def test_quarantine_emptying_the_list_is_filtered():
    search = WebSearchResult(items=[_AI_DOC_HIT], status="ok")
    items, status, reason = resolve_search_outcome(search, "price of bitcoin")
    assert (items, status, reason) == ([], "unavailable", "filtered")
    assert "reason: filtered" in _section(status, reason)


def test_quarantine_leaving_a_hit_is_untouched():
    """Positive control for the test above."""
    good = {"title": "Bitcoin price", "url": "https://example.com/b",
            "snippet": "price today", "published_at": None}
    search = WebSearchResult(items=[_AI_DOC_HIT, good], status="ok")
    items, status, reason = resolve_search_outcome(search, "price of bitcoin")
    assert items == [good]
    assert (status, reason) == ("ok", None)


def test_failed_search_passes_through_resolve_unchanged():
    search = WebSearchResult(status="unavailable", reason="timeout")
    assert resolve_search_outcome(search, "q") == ([], "unavailable", "timeout")


# ---------------------------------------------------------------------------
# 3. Junk-hit suppression (WEB_SEARCH_MIN_TERM_HITS)
# ---------------------------------------------------------------------------

_JUNK = [
    _hit("Cooking pasta", "https://example.com/1", "boil water and salt"),
    _hit("Garden tips", "https://example.com/2", "plant tomatoes in spring"),
]
_QUERY = "latest bitcoin price today"


def test_default_leaves_all_junk_untouched(monkeypatch):
    monkeypatch.delenv("WEB_SEARCH_MIN_TERM_HITS", raising=False)
    with _searxng({"results": _JUNK, "unresponsive_engines": []}):
        result = run_web_search(_QUERY)
    assert result.status == "ok"
    assert len(result.items) == 2


def test_all_low_hits_are_suppressed_when_enabled(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_MIN_TERM_HITS", "2")
    with _searxng({"results": _JUNK, "unresponsive_engines": []}):
        result = run_web_search(_QUERY)
    assert (result.items, result.status, result.reason) == (
        [], "unavailable", "low_relevance")


def test_one_good_hit_passes_everything_through(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_MIN_TERM_HITS", "2")
    good = _hit("Bitcoin price today", "https://example.com/3",
                "latest bitcoin price")
    with _searxng({"results": _JUNK + [good], "unresponsive_engines": []}):
        result = run_web_search(_QUERY)
    assert result.status == "ok"
    assert len(result.items) == 3


def test_short_query_falls_back_to_one_term(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_MIN_TERM_HITS", "2")
    one_term = _hit("Bitcoin", "https://example.com/4", "coin")
    with _searxng({"results": [one_term], "unresponsive_engines": []}):
        result = run_web_search("bitcoin?")
    assert result.status == "ok"


def test_query_without_content_terms_is_not_filtered(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_MIN_TERM_HITS", "2")
    with _searxng({"results": _JUNK, "unresponsive_engines": []}):
        result = run_web_search("what is the")
    assert result.status == "ok"


def test_greeting_dilution_does_not_suppress_a_good_hit(monkeypatch):
    monkeypatch.setenv("WEB_SEARCH_MIN_TERM_HITS", "2")
    good = _hit("Latest AI news", "https://example.com/5",
                "the latest news in artificial intelligence")
    with _searxng({"results": [good], "unresponsive_engines": []}):
        result = run_web_search(
            "Hi there, happy Friday. What's the latest news about AI?")
    assert result.status == "ok"


def test_suppression_log_has_no_hit_text(monkeypatch, caplog):
    monkeypatch.setenv("WEB_SEARCH_MIN_TERM_HITS", "2")
    with _searxng({"results": _JUNK, "unresponsive_engines": []}), \
            caplog.at_level("INFO", logger="ember.web_search"):
        run_web_search(_QUERY)
    assert "low_relevance" in caplog.text
    assert "Cooking pasta" not in caplog.text


# ---------------------------------------------------------------------------
# 4. Disclosure (#289) is unchanged and fires for the new reasons
# ---------------------------------------------------------------------------

def _postgen(status):
    return run_post_gen_pipeline(
        "Some answer.",
        intent_class="web_search",
        web_search_autonomous=True,
        used_web_search=False,
        used_vault=False,
        used_vision=False,
        web_search_status=status,
    )


def test_disclosure_prepended_for_unavailable():
    assert _postgen("unavailable").reply.startswith(
        WEB_SEARCH_UNAVAILABLE_DISCLOSURE)


def test_no_disclosure_for_ok():
    """Positive control: the assertion above fails on a different status."""
    assert WEB_SEARCH_UNAVAILABLE_DISCLOSURE not in _postgen("ok").reply


# ---------------------------------------------------------------------------
# 5. Thinking-off arm (EMBER_SEARCH_FAIL_THINK_OFF)
# ---------------------------------------------------------------------------

def _packet(status, items=None):
    return ContextPacket(user_message="q", web_items=items or [],
                         web_search_status=status)


@pytest.mark.parametrize("status", ["unavailable", "empty"])
def test_flag_on_disables_thinking_after_failure(monkeypatch, status):
    monkeypatch.setenv("EMBER_SEARCH_FAIL_THINK_OFF", "true")
    assert search_failure_think(_packet(status)) is False


def test_flag_off_leaves_thinking_alone(monkeypatch):
    monkeypatch.delenv("EMBER_SEARCH_FAIL_THINK_OFF", raising=False)
    assert search_failure_think(_packet("unavailable")) is None


def test_flag_on_leaves_successful_search_alone(monkeypatch):
    monkeypatch.setenv("EMBER_SEARCH_FAIL_THINK_OFF", "true")
    hits = [{"title": "T", "url": "https://example.com/a", "snippet": "s"}]
    assert search_failure_think(_packet("ok", hits)) is None


def test_flag_on_leaves_no_search_alone(monkeypatch):
    monkeypatch.setenv("EMBER_SEARCH_FAIL_THINK_OFF", "true")
    assert search_failure_think(_packet(None)) is None


def _bare_adapter():
    adapter = LLMAdapter.__new__(LLMAdapter)
    adapter.model = "qwen3:8b"
    return adapter


def _client_capture():
    captured: dict = {}

    def fake_chat(**kwargs):
        captured.update(kwargs)
        return {"message": {"content": "ok"}}

    return captured, fake_chat


def test_ollama_call_has_no_think_kwarg_by_default():
    adapter = _bare_adapter()
    captured, fake_chat = _client_capture()
    with patch("ollama.chat", side_effect=fake_chat), \
            patch.object(adapter, "_get_num_ctx", return_value=8192):
        adapter._chat_ollama("system", "user", model="qwen3:8b")
    assert "think" not in captured


def test_ollama_call_passes_think_false_when_asked():
    """Positive control for the test above."""
    adapter = _bare_adapter()
    captured, fake_chat = _client_capture()
    with patch("ollama.chat", side_effect=fake_chat), \
            patch.object(adapter, "_get_num_ctx", return_value=8192):
        adapter._chat_ollama("system", "user", model="qwen3:8b", think=False)
    assert captured["think"] is False


def test_ollama_stream_has_no_think_kwarg_by_default():
    adapter = _bare_adapter()
    captured, _ = _client_capture()

    def fake_stream(**kwargs):
        captured.update(kwargs)
        return iter([])

    with patch("ollama.chat", side_effect=fake_stream), \
            patch.object(adapter, "_get_num_ctx", return_value=8192):
        list(adapter._chat_ollama_stream("system", "user", model="qwen3:8b"))
    assert "think" not in captured


def test_ollama_stream_passes_think_false_when_asked():
    adapter = _bare_adapter()
    captured, _ = _client_capture()

    def fake_stream(**kwargs):
        captured.update(kwargs)
        return iter([])

    with patch("ollama.chat", side_effect=fake_stream), \
            patch.object(adapter, "_get_num_ctx", return_value=8192):
        list(adapter._chat_ollama_stream(
            "system", "user", model="qwen3:8b", think=False))
    assert captured["think"] is False


class _Stop(Exception):
    pass


def _run_iter_until_chat(adapter, packet):
    """Drive generate_response_iter up to the answer-turn _chat call and
    return the kwargs it was given."""
    seen: dict = {}

    def fake_chat(**kwargs):
        seen.update(kwargs)
        raise _Stop

    with patch.object(adapter, "_prepare_turn_prompt",
                      return_value=(MagicMock(), "sys", packet)), \
            patch.object(adapter, "_chat", side_effect=fake_chat):
        with pytest.raises(_Stop):
            list(adapter.generate_response_iter(packet))
    return seen


def _run_stream_until_chat(adapter, packet):
    seen: dict = {}

    def fake_stream(**kwargs):
        seen.update(kwargs)
        raise _Stop
        yield  # pragma: no cover

    with patch.object(adapter, "_prepare_turn_prompt",
                      return_value=(MagicMock(), "sys", packet)), \
            patch.object(adapter, "_chat_stream", side_effect=fake_stream):
        with pytest.raises(_Stop):
            list(adapter.generate_response_stream(packet))
    return seen


@pytest.mark.parametrize("runner", [_run_iter_until_chat, _run_stream_until_chat])
def test_generation_paths_pass_think_false_when_flag_on(monkeypatch, runner):
    monkeypatch.setenv("EMBER_SEARCH_FAIL_THINK_OFF", "true")
    seen = runner(_bare_adapter(), _packet("unavailable"))
    assert seen.get("think") is False


@pytest.mark.parametrize("runner", [_run_iter_until_chat, _run_stream_until_chat])
def test_generation_paths_pass_no_think_when_flag_off(monkeypatch, runner):
    monkeypatch.delenv("EMBER_SEARCH_FAIL_THINK_OFF", raising=False)
    seen = runner(_bare_adapter(), _packet("unavailable"))
    assert seen.get("think") is None


# ---------------------------------------------------------------------------
# 6. Lean-review fixes (PR #298)
# ---------------------------------------------------------------------------

from src.api.openai_adapter import _merge_confirmation_search  # noqa: E402
from src.core import config as config_mod  # noqa: E402

_HIT = {"title": "T", "url": "https://example.com/a", "snippet": "s"}


def test_think_guard_returns_none_when_packet_has_items(monkeypatch):
    """Finding 1: status unavailable with items present must not disable
    thinking. Positive control: test_flag_on_disables_thinking_after_failure
    (same status, no items) returns False."""
    monkeypatch.setenv("EMBER_SEARCH_FAIL_THINK_OFF", "true")
    assert search_failure_think(_packet("unavailable", [_HIT])) is None
    assert search_failure_think(_packet("empty", [_HIT])) is None


def test_merge_failed_confirm_does_not_override_packet_with_items():
    packet = ContextPacket(user_message="q", web_items=[_HIT],
                           web_search_status="ok")
    _merge_confirmation_search(packet, [], "unavailable", "engines_blocked")
    assert (packet.web_search_status, packet.web_search_reason) == ("ok", None)
    assert packet.web_items == [_HIT]


def test_merge_failed_confirm_applies_when_packet_has_no_items():
    """Positive control for the test above."""
    packet = ContextPacket(user_message="q")
    _merge_confirmation_search(packet, [], "unavailable", "engines_blocked")
    assert packet.web_search_status == "unavailable"
    assert packet.web_search_reason == "engines_blocked"


def test_merge_confirm_items_win():
    packet = ContextPacket(user_message="q", web_items=[{"title": "old"}],
                           web_search_status="ok")
    _merge_confirmation_search(packet, [_HIT], "ok", None)
    assert packet.web_items == [_HIT]
    assert packet.web_search_status == "ok"


def test_merge_without_confirm_status_changes_nothing():
    packet = ContextPacket(user_message="q", web_search_status="empty",
                           web_search_reason="no_results")
    _merge_confirmation_search(packet, [], None, None)
    assert (packet.web_search_status, packet.web_search_reason) == (
        "empty", "no_results")


def test_low_relevance_logs_fetched_count_and_reason(monkeypatch, caplog):
    """Finding 3."""
    monkeypatch.setenv("WEB_SEARCH_MIN_TERM_HITS", "2")
    with _searxng({"results": _JUNK, "unresponsive_engines": []}), \
            caplog.at_level("INFO", logger="ember.web_search"):
        run_web_search(_QUERY)
    assert "2 results fetched, all suppressed (low_relevance)" in caplog.text
    assert "0 results (unavailable)" not in caplog.text


def test_plain_empty_search_still_logs_result_count(monkeypatch, caplog):
    """Positive control: the generic line is still used outside low_relevance."""
    with _searxng({"results": [], "unresponsive_engines": []}), \
            caplog.at_level("INFO", logger="ember.web_search"):
        run_web_search(_QUERY)
    assert "0 results (empty)" in caplog.text
    assert "all suppressed" not in caplog.text


@pytest.fixture
def fresh_env_warnings():
    config_mod._INVALID_ENV_WARNED.clear()
    yield
    config_mod._INVALID_ENV_WARNED.clear()


@pytest.mark.parametrize("raw, expected, warns", [
    ("2", 2, False),
    (" 2 ", 2, False),
    ("0", 0, False),
    ("", 0, False),
    ("2.0", 0, True),
    ("abc", 0, True),
    ("-3", 0, True),
])
def test_min_term_hits_parsing(monkeypatch, caplog, fresh_env_warnings,
                               raw, expected, warns):
    monkeypatch.setenv("WEB_SEARCH_MIN_TERM_HITS", raw)
    with caplog.at_level("WARNING", logger="ember.config"):
        assert config_mod.get_web_search_min_term_hits() == expected
    assert ("WEB_SEARCH_MIN_TERM_HITS" in caplog.text) is warns


@pytest.mark.parametrize("raw, expected, warns", [
    ("true", True, False),
    (" true", True, False),
    ("true ", True, False),
    ("TRUE", True, False),
    ("on", True, False),
    ("1", True, False),
    ("yes", True, False),
    ("false", False, False),
    ("off", False, False),
    ("", False, False),
    ("maybe", False, True),
])
def test_think_off_flag_parsing(monkeypatch, caplog, fresh_env_warnings,
                                raw, expected, warns):
    monkeypatch.setenv("EMBER_SEARCH_FAIL_THINK_OFF", raw)
    with caplog.at_level("WARNING", logger="ember.config"):
        assert config_mod.get_search_failure_think_off() is expected
    assert ("EMBER_SEARCH_FAIL_THINK_OFF" in caplog.text) is warns


def test_invalid_env_value_warns_only_once(monkeypatch, caplog,
                                           fresh_env_warnings):
    monkeypatch.setenv("WEB_SEARCH_MIN_TERM_HITS", "abc")
    with caplog.at_level("WARNING", logger="ember.config"):
        config_mod.get_web_search_min_term_hits()
        config_mod.get_web_search_min_term_hits()
    assert caplog.text.count("WEB_SEARCH_MIN_TERM_HITS") == 1


def test_think_log_emitted_by_ollama_call_when_think_set(caplog):
    """Finding 5: the runtime log lives where think reaches the client."""
    adapter = _bare_adapter()
    _, fake_chat = _client_capture()
    with patch("ollama.chat", side_effect=fake_chat), \
            patch.object(adapter, "_get_num_ctx", return_value=8192), \
            caplog.at_level("INFO", logger="ember.llm"):
        adapter._chat_ollama("system", "user", model="qwen3:8b", think=False)
    assert "[GEN] think=False passed to ollama answer turn" in caplog.text


def test_think_log_not_emitted_when_think_unset(caplog):
    """Absence check; the test above is its positive control."""
    adapter = _bare_adapter()
    _, fake_chat = _client_capture()
    with patch("ollama.chat", side_effect=fake_chat), \
            patch.object(adapter, "_get_num_ctx", return_value=8192), \
            caplog.at_level("INFO", logger="ember.llm"):
        adapter._chat_ollama("system", "user", model="qwen3:8b")
    assert "[GEN] think=" not in caplog.text


def test_cloud_model_path_logs_no_think_line(monkeypatch, caplog):
    monkeypatch.setenv("EMBER_SEARCH_FAIL_THINK_OFF", "true")
    adapter = _bare_adapter()
    adapter.model = "claude-test"
    assert search_failure_think(_packet("unavailable")) is False
    with patch.object(adapter, "_chat_anthropic", return_value="ok") as cloud, \
            caplog.at_level("INFO", logger="ember.llm"):
        adapter._chat("system", "user", think=False)
    cloud.assert_called_once()
    assert "think" not in cloud.call_args.kwargs
    assert "[GEN] think=" not in caplog.text
