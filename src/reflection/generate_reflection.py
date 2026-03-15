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

    texts = [memory.get("text", "").strip() for memory in memories]

    unique_texts = []
    seen = set()

    for text in texts:
            key = text.lower()
            if key and key not in seen:
                unique_texts.append(text)
                seen.add(key)

    combined_text = " | ".join(unique_texts[:5])

    summary = f"Recent themes: {combined_text[:500]}"
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