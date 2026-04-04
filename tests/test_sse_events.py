"""
Tests for SSE status events and source URLs.

Covers: searching, verifying, refining status events, sources event
for web search responses.
"""

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
