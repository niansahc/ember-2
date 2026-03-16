from src.core.config import get_private_vault_path
from src.retrieval.embed_memory import embed_text
from src.retrieval.vector_index import VectorIndex


vector_index = VectorIndex()


def semantic_search(query: str, limit: int = 5, memory_type: str | None = None):
    vault = get_private_vault_path()
    embeddings_dir = vault / "embeddings"

    if not embeddings_dir.exists():
        return []

    query_embedding = embed_text(query)
    results = []

    if memory_type:
        memory_types = [memory_type]
    else:
        memory_types = [
            index_file.stem.replace("_index", "")
            for index_file in embeddings_dir.glob("*_index.json")
        ]

    for mem_type in memory_types:
        index_results = vector_index.search(
            vault_path=vault,
            memory_type=mem_type,
            query_embedding=query_embedding,
            top_k=limit,
        )

        for r in index_results:
            r["memory_type"] = mem_type
            results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]