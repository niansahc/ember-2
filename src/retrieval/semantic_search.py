from src.core.config import get_private_vault_path
from src.retrieval.embed_memory import embed_text
from src.retrieval.vector_index import VectorIndex


vector_index = VectorIndex()


def semantic_search(query: str, limit: int = 5):
    """
    Search all memory indexes for semantically similar chunks.
    """

    vault = get_private_vault_path()
    embeddings_dir = vault / "embeddings"

    if not embeddings_dir.exists():
        return []

    query_embedding = embed_text(query)

    results = []

    for index_file in embeddings_dir.glob("*_index.json"):

        memory_type = index_file.stem.replace("_index", "")

        index_results = vector_index.search(
            vault_path=vault,
            memory_type=memory_type,
            query_embedding=query_embedding,
            top_k=limit
        )

        for r in index_results:
            r["memory_type"] = memory_type
            results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)

    return results[:limit]