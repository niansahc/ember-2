"""
session_summary.py — write a session-level compression summary to the vault.

Called by LLMAdapter._maybe_compress_buffer() when mid-conversation
compression produces a summary that should be persisted as a reflection record.
"""
from __future__ import annotations

from src.memory.service import MemoryService


def write_session_summary(
    memory_service: MemoryService,
    summary: str,
    turns_compressed: int,
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
    """
    if not summary or not summary.strip():
        return

    memory_service.write(
        text=summary.strip(),
        memory_type="reflection",
        source="session_compression",
        tags=["session", "compression"],
        metadata={"cadence": "session", "turns_compressed": turns_compressed},
    )
