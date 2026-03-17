from src.core.config import get_private_vault_path
from src.retrieval.embed_memory import embed_text
from src.retrieval.vector_index import VectorIndex


vector_index = VectorIndex()


def semantic_search(
    query: str,
    limit: int = 5,
    memory_type: str | None = None,
    min_score: float | None = 0.20,
):
    vault = get_private_vault_path()
    embeddings_dir = vault / "embeddings"

    if not embeddings_dir.exists():
        return []

    query_embedding = embed_text(query)
    query_lower = query.lower().strip()
    query_terms = [t for t in query_lower.split() if t]

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
            min_score=min_score,
        )

        for r in index_results:
            content = r.get("content", "").lower()

            # Exact phrase boost
            if query_lower and query_lower in content:
                r["score"] += 0.10

            # Term hit boost (hybrid-lite)
            term_hits = sum(1 for term in query_terms if term in content)
            r["score"] += 0.05 * term_hits

            # Prefer conversation memory slightly
            if mem_type == "conversation":
                r["score"] += 0.05

            r["memory_type"] = mem_type
            results.append(r)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]