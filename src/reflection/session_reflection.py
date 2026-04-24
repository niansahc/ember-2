"""
src/reflection/session_reflection.py

Session reflection mode (ADR-009).

Generates a narrative reflection of a conversation session from the
conversation buffer contents. Distinct from session_summary.py which
does mechanical compression. This captures the arc: what was worked on,
what decisions were made, what's left open.

Stored as type="reflection", source="session_reflection", with
cadence="session" and session_id in metadata.
"""

from __future__ import annotations

import logging
from datetime import datetime

import ollama

from src.core.config import get_ember_model
from src.memory.write_memory import write_memory

logger = logging.getLogger("ember.session_reflection")

MIN_TURNS_FOR_REFLECTION = 3

SESSION_REFLECTION_PROMPT = """You are writing a session reflection for Ember, a personal intelligence system.

Given the conversation turns below, write a brief narrative summary that captures:
1. What was worked on or discussed in this session
2. What decisions were made
3. What's left open or unresolved
4. Any patterns or themes that emerged

Write in third person about the user and Ember. Be specific about content, not vague.
Keep it to 3-5 sentences. Do not use bullet points. Write it as a short narrative paragraph.

Conversation turns:
{turns_text}

Session reflection:"""


def generate_session_reflection(
    buffer_turns: list[dict],
    session_id: str | None = None,
    model: str | None = None,
) -> str | None:
    """
    Generate a narrative session reflection from conversation buffer contents.

    Parameters
    ----------
    buffer_turns : list[dict]
        Conversation turns from the buffer. Each dict has 'user' and 'assistant' keys.
    session_id : str | None
        Session ID to attach to the reflection record metadata.
    model : str | None
        Model to use for generation. Defaults to get_ember_model().

    Returns
    -------
    str | None
        The reflection text, or None if generation failed or was skipped.
    """
    if not buffer_turns or len(buffer_turns) < MIN_TURNS_FOR_REFLECTION:
        logger.info("[SESSION_REFLECT] Skipped - %d turns (minimum %d)", len(buffer_turns), MIN_TURNS_FOR_REFLECTION)
        return None

    model = model or get_ember_model()

    # Format turns into readable text
    turns_text = ""
    for i, turn in enumerate(buffer_turns, 1):
        user = turn.get("user", "")
        assistant = turn.get("assistant", "")
        turns_text += f"Turn {i}:\nUser: {user}\nEmber: {assistant}\n\n"

    # Truncate if very long
    if len(turns_text) > 8000:
        turns_text = turns_text[:8000] + "\n[... truncated]"

    prompt = SESSION_REFLECTION_PROMPT.format(turns_text=turns_text)

    try:
        # Use local model for reflection — cloud models work too but
        # this runs in the background so latency is less critical
        if model.startswith("claude-") or model.startswith("gpt-"):
            # For cloud models, use the LLM adapter's chat method directly
            # to avoid duplicating API call logic
            from src.llm.adapter import LLMAdapter
            adapter = LLMAdapter(model=model)
            reflection_text = adapter._chat(prompt, "Generate the session reflection now.", model_override=model)
        else:
            response = ollama.chat(
                model=model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": "Generate the session reflection now."},
                ],
                options={"temperature": 0.3},
            )
            reflection_text = response["message"]["content"]
    except Exception as exc:
        logger.warning("[SESSION_REFLECT] LLM call failed: %s", exc)
        return None

    if not reflection_text or not reflection_text.strip():
        return None

    reflection_text = reflection_text.strip()

    # Write to vault
    metadata = {"cadence": "session"}
    if session_id:
        metadata["session_id"] = session_id

    write_memory(
        text=reflection_text,
        memory_type="reflection",
        source="session_reflection",
        tags=["session", "reflection"],
        metadata=metadata,
    )

    logger.info("[SESSION_REFLECT] Written: %s", reflection_text[:80])
    return reflection_text
