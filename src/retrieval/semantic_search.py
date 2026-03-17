import re

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
    query_terms = extract_query_terms(query_lower)

    results = []

    if memory_type:
        memory_types = [memory_type]
    else:
        memory_types = choose_memory_types(query_lower)

    per_type_limit = max(limit * 3, 8)

    for mem_type in memory_types:
        index_results = vector_index.search(
            vault_path=vault,
            memory_type=mem_type,
            query_embedding=query_embedding,
            top_k=per_type_limit,
            min_score=min_score,
        )

        for result in index_results:
            content = result.get("content", "")
            normalized_content = normalize_text(content)

            if should_exclude_result(normalized_content):
                continue

            score = float(result.get("score", 0.0))

            if query_lower and query_lower in normalized_content:
                score += 0.10

            term_hits = sum(1 for term in query_terms if term in normalized_content)
            score += 0.03 * term_hits

            if mem_type == "conversation":
                score += 0.12
            elif mem_type == "reflection":
                score += 0.06
            elif mem_type == "ingested":
                score -= 0.05

            if is_personal_history_query(query_lower):
                if mem_type == "conversation":
                    score += 0.18
                elif mem_type == "reflection":
                    score += 0.10
                elif mem_type == "ingested":
                    score -= 0.18

            result["score"] = score
            result["memory_type"] = mem_type
            results.append(result)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def choose_memory_types(query: str) -> list[str]:
    if is_personal_history_query(query):
        return ["conversation", "reflection", "memory", "ingested"]

    return [
        index_file.stem.replace("_index", "")
        for index_file in (get_private_vault_path() / "embeddings").glob("*_index.json")
    ]


def is_personal_history_query(query: str) -> bool:
    personal_markers = (
        "have you noticed",
        "what patterns",
        "my experience",
        "lately",
        "what have we discussed",
        "what have we been discussing",
        "what have we chatted about",
        "based on my history",
        "from my history",
        "about me",
        "my ozempic",
        "my symptoms",
        "my health",
    )
    return any(marker in query for marker in personal_markers)


def extract_query_terms(query: str) -> list[str]:
    stopwords = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "have",
        "what",
        "when",
        "where",
        "which",
        "about",
        "into",
        "your",
        "just",
        "like",
        "want",
        "need",
        "does",
        "will",
        "would",
        "could",
        "should",
        "noticed",
        "patterns",
        "experience",
        "been",
        "discussing",
        "lately",
    }

    terms = re.findall(r"\b[a-z0-9]{3,}\b", query)
    return [term for term in terms if term not in stopwords]


def should_exclude_result(content: str) -> bool:
    if not content:
        return True

    if len(content) < 40:
        return True

    meta_markers = (
        "user asked:",
        "ember responded:",
        "assistant responded:",
        "assistant said:",
        "### task:",
        "generate 1-3 broad tags",
        '"user_message":',
        '"memory_items":',
        '"reflection_items":',
        '"conversation_id":',
        '"chunk_id":',
    )

    if any(marker in content for marker in meta_markers):
        return True

    if content.startswith("{") or content.startswith("["):
        return True

    if "```" in content:
        return True

    return False


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())