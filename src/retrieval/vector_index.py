import json
import os
from pathlib import Path


class VectorIndex:
    def __init__(self) -> None:
        self.max_index_size_mb = int(os.getenv("MAX_INDEX_SIZE_MB", "50"))

    def get_index_path(self, vault_path: Path, memory_type: str) -> Path:
        embeddings_dir = vault_path / "embeddings"
        embeddings_dir.mkdir(parents=True, exist_ok=True)
        return embeddings_dir / f"{memory_type}_index.json"

    def load_index(self, index_path: Path) -> list:
        if not index_path.exists():
            print(f"[VECTOR_INDEX] Missing index: {index_path}")
            return []

        try:
            size_mb = index_path.stat().st_size / (1024 * 1024)

            if size_mb > self.max_index_size_mb:
                print(
                    f"[VECTOR_INDEX] Skipping oversized index: {index_path} "
                    f"({size_mb:.2f} MB > {self.max_index_size_mb} MB)"
                )
                return []

            print(f"[VECTOR_INDEX] Loading index: {index_path} ({size_mb:.2f} MB)")

            with index_path.open("r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                return data

            print(f"[VECTOR_INDEX] Invalid index format (expected list): {index_path}")
            return []

        except (json.JSONDecodeError, OSError) as exc:
            print(f"[VECTOR_INDEX] Failed to load index {index_path}: {exc}")
            return []

    def save_index(self, index_path: Path, index_data: list) -> None:
        index_path.parent.mkdir(parents=True, exist_ok=True)

        with index_path.open("w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False)

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