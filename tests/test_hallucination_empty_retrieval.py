"""Tests for B-QUAL-004: empty retrieval grounding guard.

UAT failure: user asked "what are my top three personal goals?" with zero
goal records in vault. Ember returned three fabricated goals attributed to
vault_memory. Two-layer fix:

  1. prompt_builder.py — empty memory_items emits an explicit ZERO confidence
     block instead of a passive instruction. Gives the model an epistemic
     anchor to refuse fabrication.

  2. openai_adapter.py — when _retrieved_context is empty, the grounding
     check short-circuits to is_grounded=False rather than calling qwen3:8b
     with empty context (which fails open on parse errors).

This file exercises layer 1 directly. Layer 2 is integration code that
requires a running Ollama; it's covered by the live verification step in
the plan, not by unit tests.
"""
from __future__ import annotations

from src.context.models import ContextPacket
from src.llm.prompt_builder import PromptBuilder


def test_empty_retrieval_emits_zero_confidence_block_for_non_conversational() -> None:
    """B-QUAL-004 regression: a non-conversational query with no retrieved
    memory must include a ZERO confidence block in <vault_memory>."""
    builder = PromptBuilder()
    packet = ContextPacket(user_message="what are my top three personal goals?")

    section = builder._build_context_section(packet, is_conversational=False)

    assert "<vault_memory>" in section
    assert "</vault_memory>" in section
    assert "[Retrieval confidence:]" in section
    assert "no matches found" in section
    assert "ZERO" in section
    assert "do not fabricate" in section.lower()
    # Direct refusal phrasing must still be present
    assert "I don't have that in my memory" in section


def test_conversational_empty_state_does_not_inject_zero_block() -> None:
    """Conversational/emotional check-ins ('I'm tired', 'how are you?')
    are vault-irrelevant by design — the simple empty-state marker is the
    right behavior, not a confidence directive."""
    builder = PromptBuilder()
    packet = ContextPacket(user_message="I'm tired")

    section = builder._build_context_section(packet, is_conversational=True)

    assert "<vault_memory>" in section
    assert "[Retrieval confidence:]" not in section
    assert "ZERO" not in section
    assert "conversational" in section.lower()


def test_zero_block_explicitly_forbids_vault_attribution() -> None:
    """The ZERO confidence message must explicitly call out fabrication and
    vault attribution — those are the two failure modes from B-QUAL-004."""
    builder = PromptBuilder()
    packet = ContextPacket(user_message="tell me about my goals")

    section = builder._build_context_section(packet, is_conversational=False)

    body = section.lower()
    assert "do not fabricate" in body
    assert "vault_memory" in body


# ---------------------------------------------------------------------------
# S8: should_short_circuit_grounding helper — verifies the wiring decision
# without requiring a live Ollama instance. The helper is called from
# openai_adapter.py to decide whether to skip the grounding LLM call entirely.
# ---------------------------------------------------------------------------


def test_short_circuit_helper_fires_on_empty_string() -> None:
    from src.safety.grounding_check import should_short_circuit_grounding
    assert should_short_circuit_grounding("") is True


def test_short_circuit_helper_fires_on_whitespace_only() -> None:
    from src.safety.grounding_check import should_short_circuit_grounding
    assert should_short_circuit_grounding("   ") is True
    assert should_short_circuit_grounding("\n\t  \n") is True


def test_short_circuit_helper_does_not_fire_on_real_content() -> None:
    from src.safety.grounding_check import should_short_circuit_grounding
    assert should_short_circuit_grounding("a vault record") is False
    assert should_short_circuit_grounding("  padded content  ") is False


def test_openai_adapter_imports_and_uses_short_circuit_helper() -> None:
    """Wiring guard: ensures the helper is actually wired into the streaming
    path. If a future refactor inlines or removes the call, this catches it
    before it ships."""
    import inspect
    from src.api import openai_adapter

    src = inspect.getsource(openai_adapter)
    assert "should_short_circuit_grounding" in src, (
        "S8 wiring regression: openai_adapter.py no longer references "
        "should_short_circuit_grounding — empty-context short-circuit may be lost."
    )


# ---------------------------------------------------------------------------
# Fix 2 (2026-04-27): intent-aware gate on the ZERO block + knowledge-gap line
# ---------------------------------------------------------------------------


def test_zero_block_fires_on_status_state_intent_with_empty_memory() -> None:
    """Personal-vault intent class triggers the ZERO block via the intent
    branch of _is_personal_query (no possessive needed)."""
    builder = PromptBuilder()
    # Phrasing that wouldn't trip the lexical fallback on its own — gate is
    # passing via intent_class membership.
    packet = ContextPacket(user_message="catch me up")
    section = builder._build_context_section(
        packet, is_conversational=False, intent_class="status_state"
    )
    assert "ZERO" in section
    assert "[Retrieval confidence:]" in section


def test_zero_block_fires_on_default_intent_with_personal_possessive() -> None:
    """B-QUAL-004 protection: the canonical attack ('what are my top three
    personal goals?') routes to default intent today, but the lexical
    `\\bmy\\s+\\w+` fallback inside _is_personal_query catches it. The ZERO
    block MUST still fire — this test guards against regression."""
    builder = PromptBuilder()
    packet = ContextPacket(user_message="what are my top three personal goals?")
    section = builder._build_context_section(
        packet, is_conversational=False, intent_class="default"
    )
    assert "ZERO" in section
    assert "[Retrieval confidence:]" in section


def test_zero_block_does_not_fire_on_default_intent_general_knowledge() -> None:
    """General-knowledge query routed to default with no possessive — vault
    is not the right source. Emit a neutral empty-state marker, not the
    ZERO confidence block (which would imply vault was expected to know)."""
    builder = PromptBuilder()
    packet = ContextPacket(user_message="what's the capital of France?")
    section = builder._build_context_section(
        packet, is_conversational=False, intent_class="default"
    )
    assert "ZERO" not in section
    assert "[Retrieval confidence:]" not in section
    assert "<vault_memory>" in section


def test_zero_block_does_not_fire_on_web_search_intent() -> None:
    """web_search intent expects web results to ground the response, not
    vault. Empty memory on a web_search turn should not trigger the
    vault-grounded epistemic directive."""
    builder = PromptBuilder()
    packet = ContextPacket(user_message="what's the weather today")
    section = builder._build_context_section(
        packet, is_conversational=False, intent_class="web_search"
    )
    assert "ZERO" not in section
    assert "[Retrieval confidence:]" not in section


def test_zero_block_does_not_fire_on_conversational() -> None:
    """Preserves existing behavior — conversational check-ins get the
    simple empty-state marker even with personal_query intent."""
    builder = PromptBuilder()
    packet = ContextPacket(user_message="how are you")
    section = builder._build_context_section(
        packet, is_conversational=True, intent_class="default"
    )
    assert "ZERO" not in section
    assert "conversational" in section.lower()


def test_knowledge_gap_authority_line_uses_same_gate() -> None:
    """The 'when no vault_memory is relevant, say so directly' authority-rules
    line is gated by the same _is_personal_query check. On a general-knowledge
    query with no vault content, the line must NOT appear."""
    from src.llm.prompt_builder import (
        _AUTHORITY_RULES_KNOWLEDGE_GAP_LINE,
        _render_authority_rules,
    )
    rendered = _render_authority_rules(
        is_conversational=False,
        has_web_items=False,
        intent_class="default",
        user_message="what is the capital of France",
    )
    assert _AUTHORITY_RULES_KNOWLEDGE_GAP_LINE not in rendered


def test_knowledge_gap_authority_line_fires_on_personal_intent() -> None:
    """Companion to the above: on a personal query, the authority-rules
    knowledge-gap line still fires."""
    from src.llm.prompt_builder import (
        _AUTHORITY_RULES_KNOWLEDGE_GAP_LINE,
        _render_authority_rules,
    )
    rendered = _render_authority_rules(
        is_conversational=False,
        has_web_items=False,
        intent_class="status_state",
        user_message="what am i working on",
    )
    assert _AUTHORITY_RULES_KNOWLEDGE_GAP_LINE in rendered


def test_build_prompt_propagates_intent_class_to_authority_rules() -> None:
    """Wiring guard: build_prompt must actually pass intent_class and
    user_message through to _render_authority_rules. The unit-level
    `test_knowledge_gap_authority_line_uses_same_gate` calls _render_authority_rules
    directly; this test exercises the full call site so a missing
    keyword-arg in the build_prompt invocation gets caught."""
    from src.context.models import ContextPacket
    from src.llm.prompt_builder import (
        _AUTHORITY_RULES_KNOWLEDGE_GAP_LINE,
        PromptBuilder,
    )

    pb = PromptBuilder()

    # General-knowledge query, default intent → knowledge-gap line should
    # be SUPPRESSED through the build_prompt → _render_authority_rules path.
    prompt_general = pb.build_prompt(
        ContextPacket(user_message="what is the capital of france"),
        intent_class="default",
    )
    assert _AUTHORITY_RULES_KNOWLEDGE_GAP_LINE not in prompt_general

    # Personal query → knowledge-gap line should fire.
    prompt_personal = pb.build_prompt(
        ContextPacket(user_message="what are my goals"),
        intent_class="default",  # default intent + lexical fallback wins
    )
    assert _AUTHORITY_RULES_KNOWLEDGE_GAP_LINE in prompt_personal


def test_is_personal_query_helper() -> None:
    """Direct unit test of the gate helper. Both branches matter:
    intent_class membership AND lexical fallback."""
    from src.llm.prompt_builder import _is_personal_query

    # Intent branch
    assert _is_personal_query("status_state", "catch me up") is True
    assert _is_personal_query("reflective", "what's the trend") is True
    assert _is_personal_query("factual_recall", "find that note") is True

    # Lexical fallback branch
    assert _is_personal_query("default", "what are my top three goals") is True
    assert _is_personal_query(None, "tell me about my schedule") is True

    # Neither branch
    assert _is_personal_query("default", "capital of france") is False
    assert _is_personal_query("web_search", "weather today") is False
    assert _is_personal_query(None, None) is False
    assert _is_personal_query(None, "") is False
