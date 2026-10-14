"""
session_summary.py — write a session-level compression summary to the vault.

Called by LLMAdapter._maybe_compress_buffer() when mid-conversation
compression produces a summary that should be persisted as a reflection record.
"""
from __future__ import annotations

from src.memory.resolve_memory import find_conversation_ids_for_session
from src.memory.service import MemoryService


def write_session_summary(
    memory_service: MemoryService,
    summary: str,
    turns_compressed: int,
    session_id: str | None = None,
) -> None:
    """
    Persist a session compression summary as a reflection record.

    Parameters
    ----------
    memory_service : MemoryService
        Injected writer — avoids import coupling with the adapter.
    summary : str
        The LLM-generated summary of the compressed turns.
    turns_compressed : int
        Number of conversation turns that were summarized.
    session_id : str | None
        The session these turns belong to. Resolved against persisted
        conversation records to populate source_record_ids (ADR-015
        amendment, PR #180, implementation step 1) -- see the matching
        comment in session_reflection.py for why session_id, not per-turn
        ids, is the available correlator here.
    """
    if not summary or not summary.strip():
        return

    source_record_ids = (
        find_conversation_ids_for_session(session_id) if session_id else []
    )

    memory_service.write(
        text=summary.strip(),
        memory_type="reflection",
        source="session_compression",
        tags=["session", "compression"],
        metadata={
            "cadence": "session",
            "turns_compressed": turns_compressed,
            "source_record_ids": source_record_ids,
        },
    )
