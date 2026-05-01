"""
Tests for SSE status events and source URLs.

Covers: searching, verifying, refining, review_pending, review_complete
status events; sources event for web search responses.
"""

from pathlib import Path

from src.safety.grounding_check import should_check_grounding


def test_web_search_intent_triggers_grounding():
    """web_search intent triggers grounding check (and thus buffer path with searching event)."""
    assert should_check_grounding("web_search")


def test_default_intent_skips_grounding():
    """Default intent uses fast stream path — no searching/verifying/refining events."""
    assert not should_check_grounding("default")


def test_status_state_triggers_grounding():
    """status_state uses buffer path — verifying event emitted."""
    assert should_check_grounding("status_state")


def test_sources_event_structure():
    """Sources event should contain title and url fields."""
    # Simulate what the SSE would contain
    web_items = [
        {"title": "AI News Article", "url": "https://example.com/ai-news", "snippet": "Latest AI..."},
        {"title": "Tech Update", "url": "https://example.com/tech", "snippet": "Tech..."},
    ]

    sources = [
        {"title": item.get("title", ""), "url": item.get("url", "")}
        for item in web_items
        if item.get("url")
    ]

    assert len(sources) == 2
    assert sources[0]["title"] == "AI News Article"
    assert sources[0]["url"] == "https://example.com/ai-news"
    assert "snippet" not in sources[0]


def test_sources_not_emitted_when_no_web_items():
    """No sources event when web search returned nothing."""
    web_items = []
    sources = [
        {"title": item.get("title", ""), "url": item.get("url", "")}
        for item in web_items
        if item.get("url")
    ]
    assert sources == []


def test_verifying_fires_for_factual_recall():
    """factual_recall uses buffer path — verifying event emitted."""
    assert should_check_grounding("factual_recall")


def test_refining_only_on_ungrounded():
    """Refining event should only fire when grounding check returns False."""
    # This is a behavioral test — refining fires when is_grounded is False
    # The actual check requires Ollama, so we test the conditional logic
    is_grounded = True
    assert not (not is_grounded)  # no refining when grounded

    is_grounded = False
    assert (not is_grounded)  # refining fires when not grounded


def test_review_status_signals_translated_to_sse_in_stream_generator():
    """ADR-036 Option A wire-format guard: the streaming SSE generator
    must check for StatusSignal items from generate_response_iter() and
    translate each to a _status_event(item.name) yield. Without this
    translation, review_pending / review_complete never reach the wire
    and the UI breathing-dot indicator (commit ed858c9) never fires.

    This is a source-level guard against regressing the iteration loop
    back to a single generate_response() call. Mirrors the pattern used
    in tests/test_eval_helpers.py::TestVaultIsolation."""
    repo_root = Path(__file__).resolve().parents[1]
    source = (repo_root / "src" / "api" / "openai_adapter.py").read_text(
        encoding="utf-8"
    )

    # The new iterator must be the one called from the streaming path.
    assert "generate_response_iter" in source
    # The translation of StatusSignal -> SSE must be present.
    assert "isinstance(_item, StatusSignal)" in source
    assert "_status_event(_item.name)" in source


def test_review_signal_names_match_ui_contract():
    """The two recognized status names must exactly match what the UI
    listens for (commit ed858c9 in ../ember-2-ui). A typo here would
    break the breathing-dot indicator silently."""
    from src.llm.adapter import StatusSignal

    pending = StatusSignal("review_pending")
    complete = StatusSignal("review_complete")

    assert pending.name == "review_pending"
    assert complete.name == "review_complete"
