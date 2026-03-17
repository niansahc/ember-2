def get_memory_items(self, user_message: str) -> list[ContextItem]:
    from src.retrieval.semantic_search import semantic_search

    results = semantic_search(user_message, limit=5)
    items: list[ContextItem] = []

    for result in results:
        metadata = result.get("metadata", {})
        content = result.get("content", "")
        memory_type = result.get("memory_type", "memory")

        if memory_type == "ingested":
            item_type = "document"
        elif memory_type == "journal":
            item_type = "journal"
        else:
            item_type = memory_type

        items.append(
            ContextItem(
                id=metadata.get("chunk_id", result.get("path", "")),
                content=content,
                source=memory_type,
                item_type=item_type,
                score=result.get("score", 0.0),
                timestamp=metadata.get("created_at"),
                tags=metadata.get("tags", []),
                metadata={
                    **metadata,
                    "path": result.get("path"),
                    "memory_type": memory_type,
                },
            )
        )

    return items