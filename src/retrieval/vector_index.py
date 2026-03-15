import json
from pathlib import Path


class VectorIndex:

    def get_index_path(self, vault_path: Path, memory_type: str) -> Path:
        embeddings_dir = vault_path / "embeddings"
        embeddings_dir.mkdir(parents=True, exist_ok=True)
        return embeddings_dir / f"{memory_type}_index.json"

    def load_index(self, index_path: Path):
        if not index_path.exists():
            return []

        with open(index_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save_index(self, index_path: Path, data: list):
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    def search(self, vault_path, memory_type, query_embedding, top_k=5):
        """
        Search vector index for similar embeddings.
        """
        import numpy as np

        index_path = self.get_index_path(vault_path, memory_type)

        if not index_path.exists():
            return []

        data = self.load_index(index_path)
        results = []

        for item in data:
            embedding = np.array(item["embedding"])

            score = np.dot(query_embedding, embedding) / (
                np.linalg.norm(query_embedding) * np.linalg.norm(embedding)
            )

            results.append({
                "path": item["file_path"],
                "score": float(score)
            })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]