import json
from pathlib import Path


class VectorIndex:
    def get_index_path(self, vault_path: Path, memory_type: str) -> Path:
        embeddings_dir = vault_path / "embeddings"
        embeddings_dir.mkdir(parents=True, exist_ok=True)
        return embeddings_dir / f"{memory_type}_index.json"

    def load_index(self, index_path: Path) -> list:
        if not index_path.exists():
            return []

        try:
            with open(index_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                return data

            return []

        except (json.JSONDecodeError, OSError):
            return []

    def save_index(self, index_path: Path, index_data: list) -> None:
        index_path.parent.mkdir(parents=True, exist_ok=True)

        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)

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