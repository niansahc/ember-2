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
    normalized_query = normalize_text(query)
    query_terms = extract_query_terms(normalized_query)

    if memory_type:
        memory_types = [memory_type]
    else:
        memory_types = [
            index_file.stem.replace("_index", "")
            for index_file in embeddings_dir.glob("*_index.json")
        ]

    per_type_limit = max(limit * 4, 10)
    results = []

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
            score += lexical_relevance_bonus(normalized_query, query_terms, normalized_content)
            score += memory_type_adjustment(mem_type)
            score += source_quality_adjustment(normalized_content)
            score += query_intent_adjustment(normalized_query, mem_type, normalized_content)

            result["score"] = score
            result["memory_type"] = mem_type
            results.append(result)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def lexical_relevance_bonus(
    normalized_query: str,
    query_terms: list[str],
    normalized_content: str,
) -> float:
    bonus = 0.0

    if normalized_query and normalized_query in normalized_content:
        bonus += 0.10

    term_hits = sum(1 for term in query_terms if term in normalized_content)
    bonus += min(term_hits * 0.03, 0.18)

    return bonus


def memory_type_adjustment(mem_type: str) -> float:
    if mem_type == "conversation":
        return 0.10
    if mem_type == "reflection":
        return 0.05
    if mem_type == "memory":
        return 0.03
    if mem_type == "ingested":
        return -0.02
    return 0.0


def source_quality_adjustment(content: str) -> float:
    score = 0.0

    if content.startswith("user:"):
        score += 0.16
    elif content.startswith("assistant:"):
        score -= 0.12

    if is_question_like(content):
        score -= 0.10
    else:
        score += 0.04

    if contains_clarification_language(content):
        score -= 0.12

    if looks_like_concrete_experience(content):
        score += 0.10

    if looks_like_summary_or_instruction(content):
        score -= 0.14

    return score


def query_intent_adjustment(query: str, mem_type: str, content: str) -> float:
    score = 0.0

    if is_reflective_query(query):
        if mem_type == "conversation":
            score += 0.10
        elif mem_type == "reflection":
            score += 0.08
        elif mem_type == "ingested":
            score -= 0.08

        if content.startswith("user:"):
            score += 0.10
        elif content.startswith("assistant:"):
            score -= 0.10

    elif is_task_or_work_query(query):
        if mem_type == "ingested":
            score += 0.08
        elif mem_type == "conversation":
            score += 0.03

    return score


def is_reflective_query(query: str) -> bool:
    markers = (
        "what patterns",
        "have you noticed",
        "based on my history",
        "from my history",
        "what have we discussed",
        "what have we been discussing",
        "what have we chatted about",
        "how have i been",
        "what themes",
        "summarize",
        "recap",
        "reflect",
        "lately",
    )
    return any(marker in query for marker in markers)


def is_task_or_work_query(query: str) -> bool:
    markers = (
        "fix",
        "build",
        "implement",
        "update",
        "write",
        "refactor",
        "debug",
        "error",
        "issue",
        "architecture",
        "pipeline",
        "service",
        "api",
        "prompt",
        "context",
        "ranker",
        "retriever",
        "workflow",
        "agent",
    )
    return any(marker in query for marker in markers)


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
        "been",
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


def is_question_like(content: str) -> bool:
    markers = (
        "?",
        "what ",
        "why ",
        "how ",
        "could you",
        "can you",
        "would you",
        "should i",
        "do i",
    )
    return any(marker in content for marker in markers)


def contains_clarification_language(content: str) -> bool:
    markers = (
        "could you clarify",
        "can you clarify",
        "tell me more",
        "if you'd like",
        "consider sharing",
        "this would help",
        "can you share",
        "would you like",
    )
    return any(marker in content for marker in markers)


def looks_like_concrete_experience(content: str) -> bool:
    markers = (
        "i am",
        "i'm",
        "i was",
        "i have",
        "i've",
        "i feel",
        "i felt",
        "today",
        "yesterday",
        "this week",
        "lately",
        "worked on",
        "started",
        "finished",
        "noticed",
        "experienced",
        "having",
    )
    return any(marker in content for marker in markers)


def looks_like_summary_or_instruction(content: str) -> bool:
    markers = (
        "based on your history",
        "here’s a summary",
        "here is a summary",
        "the main themes are",
        "generate",
        "task:",
        "instructions",
        "broad tags",
        "specific tags",
    )
    return any(marker in content for marker in markers)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())