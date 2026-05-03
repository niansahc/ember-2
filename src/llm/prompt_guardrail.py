"""
src/llm/prompt_guardrail.py

Pre-generation token-overflow guardrail for the local Ollama path.

Problem: prior to this module the assembled system prompt was sent to
ollama.chat() blind. On 2026-04-26 a real production conversation
exceeded qwen3:8b's effective context window and the response degenerated
mid-sentence. B-QUAL-001 fixed Ollama's default-clamp issue (KvSize=4096)
but did not add a measurement gate against the new ceiling.

Fix: after build_prompt() returns the assembled string, estimate its
token cost. If over the input budget, cascade-trim a clone of the
ContextPacket and rebuild. Stop as soon as the estimate fits. If the
cascade exhausts and the prompt is still over, fail-open with a
structured warning rather than blocking the turn.

Cascade order (most-recoverable / least-durable first):
  1. web_items wholesale (re-searchable next turn)
  2. force-sync buffer compression (async _maybe_compress may not have run)
  3. task_items wholesale (operational)
  4. state_items minus _STATE_PROTECTED_CATEGORIES
  5. memory_items minus profile items
  6. lodestone_living suppressed via build_kwargs flag
  7. fail-open with overflow=True

Inviolable: reflection_items, profile memory items, user message, and
the static identity layer assembled inside build_prompt
(INSTRUCTION_HIERARCHY, system prompt file, identity rules, nature,
lodestone seed, authority rules, instruction section,
self-knowledge boundary, date/style/capabilities).

Local-only scope: cloud paths (Anthropic, OpenAI) bypass this guardrail
entirely. Cloud windows are 4-30x larger and the providers return clean
structured errors on overflow.
"""

from __future__ import annotations

import copy
from typing import Callable

from src.context.models import ContextPacket
from src.llm.prompt_builder import PromptBuilder

# Output cap for Ollama generations. Single source of truth so
# adapter.py stays in sync with the budget arithmetic here.
OLLAMA_NUM_PREDICT = 2048

# State categories that survive cascade step 4. Active focus, open
# loops, blockers, next actions, and pending confirmations are
# load-bearing for current operational context; dropping them silently
# would degrade the response in ways the user would notice immediately.
_STATE_PROTECTED_CATEGORIES = frozenset({
    "current_focus",
    "open_loop",
    "blocker",
    "next_action",
    "pending_confirmation",
})


def estimate_tokens(text: str) -> int:
    """Conservative char-count ceiling: len(text) // 3.

    English averages ~4 chars/token; using 3 gives ~33% safety margin
    so the guardrail trips earlier than strictly needed on JSON, code,
    and markdown (which tokenize denser than prose). The estimator IS
    the safety margin -- no need to layer additional headroom on top
    of an already-conservative measure.
    """
    if not text:
        return 0
    return len(text) // 3


def get_input_budget(num_ctx: int, num_predict: int = OLLAMA_NUM_PREDICT) -> int:
    """Return the input token budget for an Ollama call.

    budget = (num_ctx - num_predict) * 0.95 with sanity floor 1024.

    The 5% headroom absorbs estimator drift on turns where the prompt
    is heavy on JSON/markdown/code (denser than prose). The 1024 floor
    prevents a misconfigured user preference from collapsing the budget
    to zero or negative.
    """
    raw = int((num_ctx - num_predict) * 0.95)
    return max(1024, raw)


def trim_to_fit(
    packet: ContextPacket,
    model: str,
    num_ctx: int,
    builder: PromptBuilder,
    build_kwargs: dict,
    buffer_compress_callback: Callable[[], None] | None = None,
) -> tuple[str, ContextPacket, dict]:
    """Build prompt; cascade-trim a clone of packet if over budget.

    The original packet is NOT mutated. Returns a tuple of
    (system_prompt, possibly-trimmed-packet, telemetry_dict). Caller
    should use the returned packet for downstream review-context
    construction so signals like _is_vault_grounded honestly reflect
    what the model actually saw.
    """
    budget = get_input_budget(num_ctx)
    working_packet = _clone_packet(packet)
    working_kwargs = dict(build_kwargs)

    system_prompt = builder.build_prompt(working_packet, **working_kwargs)
    initial_estimate = estimate_tokens(system_prompt)

    if initial_estimate <= budget:
        return system_prompt, working_packet, _telemetry(
            model=model,
            budget=budget,
            initial=initial_estimate,
            final=initial_estimate,
            dropped=[],
            iterations=0,
            overflow=False,
        )

    sections_dropped: list[str] = []
    iterations = 0

    # Step 1: drop web_items wholesale
    if working_packet.web_items:
        working_packet.web_items = []
        sections_dropped.append("web_items")
        iterations += 1
        system_prompt = builder.build_prompt(working_packet, **working_kwargs)
        if estimate_tokens(system_prompt) <= budget:
            return system_prompt, working_packet, _telemetry(
                model, budget, initial_estimate,
                estimate_tokens(system_prompt),
                sections_dropped, iterations, overflow=False,
            )

    # Step 2: force-sync buffer compression
    if buffer_compress_callback is not None:
        buffer_compress_callback()
        sections_dropped.append("buffer_compress")
        iterations += 1
        system_prompt = builder.build_prompt(working_packet, **working_kwargs)
        if estimate_tokens(system_prompt) <= budget:
            return system_prompt, working_packet, _telemetry(
                model, budget, initial_estimate,
                estimate_tokens(system_prompt),
                sections_dropped, iterations, overflow=False,
            )

    # Step 3: drop task_items wholesale
    if working_packet.task_items:
        working_packet.task_items = []
        sections_dropped.append("task_items")
        iterations += 1
        system_prompt = builder.build_prompt(working_packet, **working_kwargs)
        if estimate_tokens(system_prompt) <= budget:
            return system_prompt, working_packet, _telemetry(
                model, budget, initial_estimate,
                estimate_tokens(system_prompt),
                sections_dropped, iterations, overflow=False,
            )

    # Step 4: drop state_items not in _STATE_PROTECTED_CATEGORIES
    if any(
        s.category not in _STATE_PROTECTED_CATEGORIES
        for s in working_packet.state_items
    ):
        working_packet.state_items = [
            s for s in working_packet.state_items
            if s.category in _STATE_PROTECTED_CATEGORIES
        ]
        sections_dropped.append("state_items_unprotected")
        iterations += 1
        system_prompt = builder.build_prompt(working_packet, **working_kwargs)
        if estimate_tokens(system_prompt) <= budget:
            return system_prompt, working_packet, _telemetry(
                model, budget, initial_estimate,
                estimate_tokens(system_prompt),
                sections_dropped, iterations, overflow=False,
            )

    # Step 5: drop memory_items that are NOT profile
    if any(
        getattr(m, "memory_type", None) != "profile"
        for m in working_packet.memory_items
    ):
        working_packet.memory_items = [
            m for m in working_packet.memory_items
            if getattr(m, "memory_type", None) == "profile"
        ]
        sections_dropped.append("memory_items_non_profile")
        iterations += 1
        system_prompt = builder.build_prompt(working_packet, **working_kwargs)
        if estimate_tokens(system_prompt) <= budget:
            return system_prompt, working_packet, _telemetry(
                model, budget, initial_estimate,
                estimate_tokens(system_prompt),
                sections_dropped, iterations, overflow=False,
            )

    # Step 6: suppress lodestone_living via build_kwargs flag
    if not working_kwargs.get("suppress_lodestone_living"):
        working_kwargs["suppress_lodestone_living"] = True
        sections_dropped.append("lodestone_living")
        iterations += 1
        system_prompt = builder.build_prompt(working_packet, **working_kwargs)
        if estimate_tokens(system_prompt) <= budget:
            return system_prompt, working_packet, _telemetry(
                model, budget, initial_estimate,
                estimate_tokens(system_prompt),
                sections_dropped, iterations, overflow=False,
            )

    # Step 7: fail-open with structured per-section breakdown
    final_estimate = estimate_tokens(system_prompt)
    return system_prompt, working_packet, _telemetry(
        model, budget, initial_estimate, final_estimate,
        sections_dropped, iterations, overflow=True,
        per_section=_per_section_estimates(working_packet, system_prompt),
    )


def _clone_packet(packet: ContextPacket) -> ContextPacket:
    """Shallow clone with copied list references so mutating the working
    packet's lists never leaks into the caller's original packet.
    Individual items are shared; only the list objects are new."""
    cloned = copy.copy(packet)
    cloned.memory_items = list(packet.memory_items)
    cloned.reflection_items = list(packet.reflection_items)
    cloned.state_items = list(packet.state_items)
    cloned.task_items = list(packet.task_items)
    cloned.web_items = list(packet.web_items)
    cloned.image_data = list(packet.image_data)
    return cloned


def _telemetry(
    model: str,
    budget: int,
    initial: int,
    final: int,
    dropped: list[str],
    iterations: int,
    overflow: bool,
    per_section: dict[str, int] | None = None,
) -> dict:
    return {
        "model": model,
        "budget": budget,
        "initial_estimate": initial,
        "final_estimate": final,
        "sections_dropped": list(dropped),
        "iterations": iterations,
        "overflow": overflow,
        "per_section_estimates": per_section,
    }


def _per_section_estimates(packet: ContextPacket, prompt: str) -> dict[str, int]:
    """Estimated tokens per packet section at failure time, plus a
    derived static_layer estimate (everything in the prompt that isn't
    a packet-driven section). Diagnostic only, populated on overflow.

    static_layer is a coarse derivation: total_prompt_tokens minus the
    sum of packet section estimates. It approximates the cost of the
    identity / nature / system-prompt / authority / instruction layer
    plus all the section formatting overhead. Useful for spotting the
    'oversized identity files' failure mode.
    """
    web = sum(estimate_tokens(_repr_web_item(item)) for item in packet.web_items)
    tasks = sum(estimate_tokens(getattr(t, "title", "")) for t in packet.task_items)
    state = sum(estimate_tokens(s.text) for s in packet.state_items)
    memory = sum(estimate_tokens(m.content) for m in packet.memory_items)
    reflection = sum(estimate_tokens(r.content) for r in packet.reflection_items)
    packet_sum = web + tasks + state + memory + reflection
    static = max(0, estimate_tokens(prompt) - packet_sum)
    return {
        "web_items": web,
        "task_items": tasks,
        "state_items": state,
        "memory_items": memory,
        "reflection_items": reflection,
        "static_layer": static,
    }


def _repr_web_item(web_item: dict) -> str:
    """Approximate text representation of a web search item for sizing."""
    if not isinstance(web_item, dict):
        return ""
    parts = [
        str(web_item.get("title", "")),
        str(web_item.get("snippet", "")),
        str(web_item.get("url", "")),
    ]
    return " ".join(parts)
