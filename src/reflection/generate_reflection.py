from __future__ import annotations

from src.memory.service import MemoryService


memory_service = MemoryService()


def generate_reflection(
    memory_type: str = "journal",
    limit: int = 50,
    store: bool = True,
    cadence: str = "daily",
):
    memories = memory_service.read(memory_type=memory_type, limit=limit)

    normalized_memories = []
    seen = set()

    for memory in memories:
        text = _extract_memory_text(memory).strip()
        if not text:
            continue

        key = text.lower()
        if key in seen:
            continue

        seen.add(key)
        normalized_memories.append(
            {
                "text": text,
                "timestamp": memory.get("timestamp") or memory.get("created_at"),
                "source": memory.get("source", memory_type),
                "title": memory.get("title") or memory.get("metadata", {}).get("title"),
            }
        )

    if not normalized_memories:
        return {
            "summary": "No memories available for reflection.",
            "memory_count": 0,
            "source_type": memory_type,
        }

    selected_texts = [item["text"] for item in normalized_memories[:8]]
    combined_text = " | ".join(selected_texts)

    summary = f"Recent themes: {combined_text[:800]}"

    reflection = {
        "summary": summary,
        "memory_count": len(normalized_memories),
        "source_type": memory_type,
    }

    if store:
        memory_service.write(
            text=summary,
            memory_type="reflection",
            source="reflection_engine",
            tags=["reflection", cadence],
            metadata={
                "cadence": cadence,
                "source_type": memory_type,
                "memory_count": len(normalized_memories),
            },
        )

    return reflection


def _extract_memory_text(memory: dict) -> str:
    if not isinstance(memory, dict):
        return ""

    if memory.get("text"):
        return str(memory["text"])

    if memory.get("content"):
        return str(memory["content"])

    metadata = memory.get("metadata", {})
    if isinstance(metadata, dict):
        if metadata.get("text"):
            return str(metadata["text"])
        if metadata.get("content"):
            return str(metadata["content"])

    return ""