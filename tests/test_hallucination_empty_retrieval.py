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
