from src.memory.service import MemoryService

memory_service = MemoryService()


def generate_reflection(memory_type="journal", limit=50, store=True, cadence="daily"):
    memories = memory_service.read(memory_type=memory_type, limit=limit)

    if not memories:
        return {
            "summary": "No memories available for reflection.",
            "memory_count": 0,
            "source_type": memory_type,
        }

    texts = [memory.get("text", "") for memory in memories]
    combined_text = " ".join(texts)

    summary = f"Recent themes from {memory_type} memories: {combined_text[:500]}"

    reflection = {
        "summary": summary,
        "memory_count": len(memories),
        "source_type": memory_type,
    }

    if store:
        memory_service.write(
            text=summary,
            memory_type="reflection",
            source="reflection_engine",
            tags=["reflection"],
            metadata={"cadence": cadence}


    )

    return reflection