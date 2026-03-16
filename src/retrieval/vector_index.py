import json
from pathlib import Path

import numpy as np


class VectorIndex:
    def get_index_path(self, vault_path: Path, memory_type: str) -> Path:
        embeddings_dir = vault_path / "embeddings"
        embeddings_dir.mkdir(parents=True, exist_ok=True)
        return embeddings_dir / f"{memory_type}_index.json"

    def load_index(self, index_path: Path) -> list:
        if not index_path.exists():
            return []
        with open(index_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_index(self, index_path: Path, data: list) -> None:
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def load_chunk(self, file_path: str):
        path = Path(file_path)

        if not path.exists():
            return {"content": "", "metadata": {}}

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return {
            "content": data.get("content", ""),
            "metadata": data.get("metadata", {})
        }

    def search(
        self,
        vault_path: Path,
        memory_type: str,
        query_embedding,
        top_k: int = 5,
        min_score: float | None = None,
    ) -> list:
        index_path = self.get_index_path(vault_path, memory_type)
        data = self.load_index(index_path)
        results = []

        query_embedding = np.array(query_embedding)
        query_norm = np.linalg.norm(query_embedding)

        if query_norm == 0:
            return []

        for item in data:
            embedding = np.array(item["embedding"])
            embedding_norm = np.linalg.norm(embedding)

            if embedding_norm == 0:
                continue

            score = np.dot(query_embedding, embedding) / (query_norm * embedding_norm)

            if min_score is not None and score < min_score:
                continue

            chunk = self.load_chunk(item["file_path"])

            results.append({
                "path": item["file_path"],
                "score": float(score),
                "content": chunk["content"],
                "metadata": chunk["metadata"],
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]