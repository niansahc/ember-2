"""
Tests for buffer-then-stream with grounding check (ADR-019).

Covers: intent-class triggering in streaming path, grounding check
integration points, sources emission for web search.
"""

from src.safety.grounding_check import should_check_grounding, GROUNDING_CHECK_INTENTS


def test_grounding_triggered_for_factual_recall():
    assert should_check_grounding("factual_recall")


def test_grounding_triggered_for_web_search():
    assert should_check_grounding("web_search")


def test_grounding_triggered_for_status_state():
    assert should_check_grounding("status_state")


def test_grounding_not_triggered_for_default():
    assert not should_check_grounding("default")


def test_grounding_not_triggered_for_recent():
    assert not should_check_grounding("recent")


def test_grounding_not_triggered_for_activity():
    assert not should_check_grounding("activity")


def test_grounding_not_triggered_for_recent_activity():
    assert not should_check_grounding("recent_activity")


def test_classify_query_provides_intent_class():
    """Verify classify_query returns a policy with a name (intent class)."""
    from src.context.policies import classify_query

    policy = classify_query("What do you know about me?")
    assert hasattr(policy, "name")
    assert isinstance(policy.name, str)


def test_context_packet_has_memory_items_with_content():
    """Verify memory items have content attribute for retrieved_context extraction."""
    from src.context.models import ContextItem

    item = ContextItem(
        id="test",
        content="some vault content",
        source="test",
        item_type="conversation",
    )
    assert hasattr(item, "content")
    assert item.content == "some vault content"
