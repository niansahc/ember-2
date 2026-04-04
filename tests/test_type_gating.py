"""
Tests for ADR-018: Intent-aware memory type gating.

Covers: min_score floor, eligible_memory_types filtering,
suppress_memory_types filtering, profile bypass, empty context
message in prompt builder, backward-compatible defaults.
"""

from src.context.models import ContextItem, ContextPacket
from src.context.policies import ContextPolicy, classify_query
from src.context.service import ContextService


def _make_item(memory_type: str, score: float = 0.5, content: str = "test content that is long enough to pass filters") -> ContextItem:
    return ContextItem(
        id=f"test-{memory_type}",
        content=content,
        source=memory_type,
        item_type=memory_type,
        memory_type=memory_type,
        score=score,
    )


# ── Min score floor ─────────────────────────────────────────────────────


def test_min_score_floor_excludes_low_score_items():
    service = ContextService()
    policy = ContextPolicy(name="test", min_score=0.25)

    items = [
        _make_item("conversation", score=0.30),
        _make_item("ingested", score=0.10),
        _make_item("reflection", score=0.50),
    ]

    filtered = service._apply_type_gate(items, policy)
    assert len(filtered) == 2
    assert all(i.score >= 0.25 for i in filtered)


def test_min_score_floor_default_is_0_25():
    policy = ContextPolicy(name="default")
    assert policy.min_score == 0.25


# ── Eligible memory types ──────────────────────────────────────────────


def test_eligible_memory_types_filters_non_eligible():
    service = ContextService()
    policy = ContextPolicy(
        name="test",
        eligible_memory_types=["state", "profile", "conversation"],
        min_score=0.0,
    )

    items = [
        _make_item("state"),
        _make_item("profile"),
        _make_item("conversation"),
        _make_item("ingested"),
        _make_item("reflection"),
    ]

    filtered = service._apply_type_gate(items, policy)
    types = {i.memory_type for i in filtered}
    assert "state" in types
    assert "profile" in types
    assert "conversation" in types
    assert "ingested" not in types
    assert "reflection" not in types


def test_eligible_memory_types_none_means_all_eligible():
    service = ContextService()
    policy = ContextPolicy(name="test", eligible_memory_types=None, min_score=0.0)

    items = [
        _make_item("state"),
        _make_item("ingested"),
        _make_item("reflection"),
        _make_item("journal"),
    ]

    filtered = service._apply_type_gate(items, policy)
    assert len(filtered) == 4


# ── Suppress memory types ──────────────────────────────────────────────


def test_suppress_memory_types_removes_suppressed():
    service = ContextService()
    policy = ContextPolicy(
        name="test",
        suppress_memory_types=["journal", "ingested"],
        min_score=0.0,
    )

    items = [
        _make_item("conversation"),
        _make_item("journal"),
        _make_item("ingested"),
        _make_item("reflection"),
    ]

    filtered = service._apply_type_gate(items, policy)
    types = {i.memory_type for i in filtered}
    assert "journal" not in types
    assert "ingested" not in types
    assert "conversation" in types
    assert "reflection" in types


# ── Profile bypass ──────────────────────────────────────────────────────


def test_profile_bypasses_eligible_memory_types():
    service = ContextService()
    policy = ContextPolicy(
        name="test",
        eligible_memory_types=["conversation"],
        min_score=0.0,
    )

    items = [
        _make_item("conversation"),
        _make_item("profile"),
    ]

    filtered = service._apply_type_gate(items, policy)
    types = {i.memory_type for i in filtered}
    assert "profile" in types
    assert "conversation" in types


def test_profile_bypasses_suppress_memory_types():
    service = ContextService()
    policy = ContextPolicy(
        name="test",
        suppress_memory_types=["profile"],
        min_score=0.0,
    )

    items = [_make_item("profile")]

    filtered = service._apply_type_gate(items, policy)
    assert len(filtered) == 1
    assert filtered[0].memory_type == "profile"


def test_profile_bypasses_min_score():
    service = ContextService()
    policy = ContextPolicy(name="test", min_score=0.50)

    items = [
        _make_item("profile", score=0.10),
        _make_item("conversation", score=0.10),
    ]

    filtered = service._apply_type_gate(items, policy)
    assert len(filtered) == 1
    assert filtered[0].memory_type == "profile"


# ── Policy defaults backward compatible ─────────────────────────────────


def test_default_policy_has_backward_compatible_fields():
    policy = ContextPolicy(name="default")
    assert policy.eligible_memory_types is None
    assert policy.suppress_memory_types == []
    assert policy.min_score == 0.25


# ── Classify query sets type gating fields ──────────────────────────────


def test_status_state_policy_sets_eligible_types():
    policy = classify_query("What am I working on?")
    assert policy.name == "status_state"
    assert policy.eligible_memory_types is not None
    assert "state" in policy.eligible_memory_types
    assert "profile" in policy.eligible_memory_types


def test_factual_recall_policy_sets_eligible_types():
    policy = classify_query("What did I say about that?")
    assert policy.name == "factual_recall"
    assert policy.eligible_memory_types is not None
    assert "ingested" in policy.eligible_memory_types
    assert "conversation" in policy.eligible_memory_types


def test_reflective_policy_allows_all_types():
    policy = classify_query("What patterns have you noticed?")
    assert policy.name == "reflective"
    assert policy.eligible_memory_types is None


def test_default_policy_allows_all_types():
    policy = classify_query("Hello, how are you?")
    assert policy.name == "default"
    assert policy.eligible_memory_types is None


# ── Empty context message in prompt builder ─────────────────────────────


def test_empty_memory_renders_absence_signal():
    from src.llm.prompt_builder import PromptBuilder

    builder = PromptBuilder()
    packet = ContextPacket(
        user_message="test",
        memory_items=[],
        reflection_items=[],
        state_items=[],
        task_items=[],
        web_items=[],
        image_data=[],
    )

    prompt = builder.build_prompt(packet)
    assert "No relevant memory found for this query" in prompt
    assert "I don't have that in my memory" in prompt
