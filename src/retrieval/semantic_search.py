import numpy as np

from src.core.config import get_private_vault_path
from src.retrieval.embed_memory import embed_text
from src.retrieval.vector_index import VectorIndex


vector_index = VectorIndex()


def cosine_similarity(a, b):
    a = np.array(a)
    b = np.array(b)

    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


def semantic_search(query: str, limit: int = 5):
    vault = get_private_vault_path()
    embeddings_dir = vault / "embeddings"

    if not embeddings_dir.exists():
        return []

    query_embedding = embed_text(query)

    results = []

    for index_file in embeddings_dir.glob("journal_index.json"):

        index_data = vector_index.load_index(index_file)

        for item in index_data:

            similarity = cosine_similarity(query_embedding, item["embedding"])

            results.append({
                "similarity": float(similarity),
                "memory": item
            })

    results.sort(key=lambda x: x["similarity"], reverse=True)

    return results[:limit]