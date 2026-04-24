"""
src/retrieval/vector_index.py

Vector index management for semantic search.

Indexes are JSON files containing embeddings and text for each memory type.
They are cached in memory after first load to avoid re-reading from disk
on every query. The cache is invalidated when an index is written to.
"""

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("ember.vector_index")

# Module-level index cache: path_string -> list of index entries
_index_cache: dict[str, list] = {}


def clear_index_cache(index_path: str | None = None) -> None:
    """
    Clear the in-memory index cache.

    If index_path is provided, only that index is cleared.
    If None, the entire cache is cleared.
    """
    if index_path:
        key = str(index_path)
        if key in _index_cache:
            del _index_cache[key]
            logger.info("[VECTOR_INDEX] Cache cleared for: %s", key)
    else:
        _index_cache.clear()
        logger.info("[VECTOR_INDEX] Full cache cleared")


class VectorIndex:
    def __init__(self) -> None:
        self.max_index_size_mb = int(os.getenv("MAX_INDEX_SIZE_MB", "50"))

    def get_index_path(self, vault_path: Path, memory_type: str) -> Path:
        embeddings_dir = vault_path / "embeddings"
        embeddings_dir.mkdir(parents=True, exist_ok=True)
        return embeddings_dir / f"{memory_type}_index.json"

    def load_index(self, index_path: Path) -> list:
        key = str(index_path)

        # Check cache first
        if key in _index_cache:
            logger.info("[VECTOR_INDEX] Cache hit: %s", index_path.name)
            return _index_cache[key]

        # Cache miss — load from disk
        if not index_path.exists():
            logger.info("[VECTOR_INDEX] Missing index: %s", index_path)
            return []

        try:
            size_mb = index_path.stat().st_size / (1024 * 1024)

            if size_mb > self.max_index_size_mb:
                logger.warning(
                    "[VECTOR_INDEX] Skipping oversized index: %s (%.2f MB > %d MB)",
                    index_path, size_mb, self.max_index_size_mb,
                )
                return []

            logger.info("[VECTOR_INDEX] Cache miss - loading: %s (%.2f MB)", index_path.name, size_mb)

            with index_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                _index_cache[key] = data
                return data

            logger.warning("[VECTOR_INDEX] Invalid index format (expected list): %s", index_path)
            return []

        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("[VECTOR_INDEX] Failed to load index %s: %s", index_path, exc)
            return []

    def save_index(self, index_path: Path, index_data: list) -> None:
        index_path.parent.mkdir(parents=True, exist_ok=True)

        with index_path.open("w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False)

        # Invalidate cache for this index — next read will load fresh data
        key = str(index_path)
        if key in _index_cache:
            del _index_cache[key]
            logger.info("[VECTOR_INDEX] Cache invalidated after write: %s", index_path.name)

    def search(
        self,
        vault_path: Path,
        memory_type: str,
        query_embedding: list[float],
        top_k: int = 5,
        min_score: float | None = None,
    ) -> list[dict]:
        index_path = self.get_index_path(vault_path, memory_type)
        index_data = self.load_index(index_path)

        if not index_data:
            return []

        scored_results = []

        for item in index_data:
            embedding = item.get("embedding")

            if not embedding:
                continue

            score = self.cosine_similarity(query_embedding, embedding)

            if min_score is not None and score < min_score:
                continue

            scored_results.append(
                {
                    "score": score,
                    "path": item.get("file_path"),
                    "content": item.get("text", ""),
                    "metadata": item.get("metadata", {}),
                }
            )

        scored_results.sort(key=lambda x: x["score"], reverse=True)
        return scored_results[:top_k]

    def cosine_similarity(self, a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0

        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return dot_product / (norm_a * norm_b)
