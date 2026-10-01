"""
src/context/render_window.py

How much of a context packet the prompt actually renders.

The packet is a CANDIDATE SET. The prompt renders a slice of it, and the two
numbers differ: `ContextService._memory_limit_for_policy` returns 4 to 6
depending on policy, and the prompt renders 4 non-profile memory records and one
reflection. On every policy except `reflective`, one to two memory records per
query are selected, ranked, scored through the whole composition, and then
discarded at render.

This module owns those two numbers, and exists because they were bare literals
restated in four places. Issue #227 is the history: retrieval stats were being
written for the packet rather than the render, so a record the model never saw
took a heat promotion -- and under the shipped formula one write forces
`recency = 1.0` and `heat >= 0.625`, clearing the hot threshold outright. PR #238
fixed that by deferring the write until the prompt reports what it rendered.

The same divergence then turned out to be live in the trace harness, which
measured delivery off `packet.memory_items`. That made the Sobol delivery
endpoint blind to the one boundary that decides whether the model sees a record
at all: a candidate moving between rank 4 and rank 5 changes nothing about packet
membership, so the measured distance was zero for the most consequential
reordering there is. Every delivery ST taken before this module existed was
measured that way.

So: one definition, imported by the prompt builder that renders it and by the
harness that measures it.

WHAT THIS DOES NOT MODEL. The rendered set is not a pure function of the packet.
`src/llm/prompt_guardrail.trim_to_fit` rebuilds the prompt up to seven times on
the local-Ollama path, dropping sections each round, and its fifth step filters
memory items to profile-only. Only the last build is what the model receives.
These functions describe the window the prompt applies to a packet, which is the
whole story for the cloud path and for any local turn under budget, and an
over-estimate for a trimmed one. Anything that needs the trimmed truth has to
read `ContextPacket.delivered_items` after the final build instead.
"""

from __future__ import annotations

# Non-profile memory records the prompt renders. Profile is uncapped -- ADR-046
# gives it guaranteed slots that are not charged against the policy's memory
# limit, so it is prepended whole.
MEMORY_RENDER_SLOTS = 4

# Reflections the prompt renders, against a reflection_limit of 1 to 3.
REFLECTION_RENDER_SLOTS = 1


def rendered_memory_window(memory_items) -> tuple[list, list]:
    """The memory records the prompt renders: (profile, capped non-profile).

    Takes the whole mixed `memory_items` list rather than a pre-partitioned one.
    The partition is inseparable from the slice -- the cap applies to non-profile
    records only, and profile records are uncapped -- so a signature that took
    them separately would push the partition back out to every caller.

    Returns the two lists rather than their concatenation because the prompt
    renders them in separate sections and so needs them apart. Returning
    `profile + other` meant the renderer immediately re-split the result by the
    same predicate, which put the partition rule in two modules -- the drift this
    module exists to prevent, one call frame later. Callers that want the whole
    rendered set concatenate.
    """
    profile = [i for i in memory_items if i.memory_type == "profile"]
    other = [i for i in memory_items if i.memory_type != "profile"]
    return profile, other[:MEMORY_RENDER_SLOTS]


def rendered_reflection_window(reflection_items) -> list:
    """The reflections the prompt renders.

    Separate from the memory window because the prompt renders the two channels
    in separate sections with separate early returns: a packet can render
    reflections and no memory, or the reverse.
    """
    return list(reflection_items[:REFLECTION_RENDER_SLOTS])
