import re

from src.core.config import get_private_vault_path
from src.retrieval.embed_memory import embed_text
from src.retrieval.sqlite_vector_store import SqliteVectorStore
from src.retrieval.vector_index import VectorIndex


vector_index = VectorIndex()

_sqlite_store: SqliteVectorStore | None = None
_memory_store: SqliteVectorStore | None = None

# Memory types stored in memory.db (migrated from JSON indexes)
SQLITE_MEMORY_TYPES = {"conversation", "profile", "reflection", "journal"}


def _get_sqlite_store() -> SqliteVectorStore | None:
    """Singleton for ingested.db (ingested content)."""
    global _sqlite_store
    if _sqlite_store is not None:
        return _sqlite_store
    vault = get_private_vault_path()
    db_path = vault / "embeddings" / "ingested.db"
    if not db_path.exists():
        return None
    _sqlite_store = SqliteVectorStore(db_path)
    return _sqlite_store


def _get_memory_store() -> SqliteVectorStore | None:
    """Singleton for memory.db (conversation, profile, reflection, journal)."""
    global _memory_store
    if _memory_store is not None:
        return _memory_store
    vault = get_private_vault_path()
    db_path = vault / "embeddings" / "memory.db"
    if not db_path.exists():
        return None
    _memory_store = SqliteVectorStore(db_path)
    return _memory_store


def semantic_search(
    query: str,
    limit: int = 5,
    memory_type: str | None = None,
    min_score: float | None = 0.20,
    query_embedding: list[float] | None = None,
):
    vault = get_private_vault_path()
    embeddings_dir = vault / "embeddings"

    if not embeddings_dir.exists():
        return []

    if query_embedding is None:
        query_embedding = embed_text(query)
    normalized_query = normalize_text(query)
    query_terms = extract_query_terms(normalized_query)

    per_type_limit = max(limit * 4, 10)
    results = []

    # Search memory.db for migrated types (conversation, profile, reflection, journal)
    memory_store = _get_memory_store()
    if memory_store is not None:
        if memory_type and memory_type in SQLITE_MEMORY_TYPES:
            # Search a specific migrated type
            sqlite_results = memory_store.search(
                query_embedding=query_embedding,
                limit=per_type_limit,
                memory_type=memory_type,
            )
            for result in sqlite_results:
                content = result.get("content", "")
                normalized_content = normalize_text(content)

                if should_exclude_result(normalized_content):
                    continue

                metadata = result.get("metadata", {})
                mem_type = result.get("memory_type", memory_type)
                raw_score = float(result.get("score", 0.0))
                score = raw_score
                score += lexical_relevance_bonus(normalized_query, query_terms, normalized_content, raw_query=query)
                score += memory_type_adjustment(mem_type)
                score += source_quality_adjustment(normalized_content, metadata)
                score += query_intent_adjustment(normalized_query, mem_type, normalized_content)

                result["score"] = score
                result["raw_score"] = raw_score
                result["memory_type"] = mem_type
                results.append(result)

        elif memory_type is None:
            # Search all migrated types
            for mem_type in SQLITE_MEMORY_TYPES:
                sqlite_results = memory_store.search(
                    query_embedding=query_embedding,
                    limit=per_type_limit,
                    memory_type=mem_type,
                )
                for result in sqlite_results:
                    content = result.get("content", "")
                    normalized_content = normalize_text(content)

                    if should_exclude_result(normalized_content):
                        continue

                    metadata = result.get("metadata", {})
                    raw_score = float(result.get("score", 0.0))
                    score = raw_score
                    score += lexical_relevance_bonus(normalized_query, query_terms, normalized_content, raw_query=query)
                    score += memory_type_adjustment(mem_type)
                    score += source_quality_adjustment(normalized_content, metadata)
                    score += query_intent_adjustment(normalized_query, mem_type, normalized_content)

                    result["score"] = score
                    result["raw_score"] = raw_score
                    result["memory_type"] = mem_type
                    results.append(result)

    # Fallback: search any remaining JSON indexes for non-migrated, non-ingested types
    if memory_type and memory_type not in SQLITE_MEMORY_TYPES and memory_type != "ingested":
        index_results = vector_index.search(
            vault_path=vault,
            memory_type=memory_type,
            query_embedding=query_embedding,
            top_k=per_type_limit,
            min_score=min_score,
        )
        for result in index_results:
            content = result.get("content", "")
            normalized_content = normalize_text(content)

            if should_exclude_result(normalized_content):
                continue

            raw_score = float(result.get("score", 0.0))
            score = raw_score
            score += lexical_relevance_bonus(normalized_query, query_terms, normalized_content, raw_query=query)
            score += memory_type_adjustment(memory_type)
            metadata = result.get("metadata", {})
            score += source_quality_adjustment(normalized_content, metadata)
            score += query_intent_adjustment(normalized_query, memory_type, normalized_content)

            result["score"] = score
            result["raw_score"] = raw_score
            result["memory_type"] = memory_type
            results.append(result)
    elif memory_type is None:
        # Search any remaining JSON indexes (non-migrated, non-ingested)
        json_types = [
            index_file.stem.replace("_index", "")
            for index_file in embeddings_dir.glob("*_index.json")
        ]
        json_types = [t for t in json_types if t != "ingested" and t not in SQLITE_MEMORY_TYPES]

        for mem_type in json_types:
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

                raw_score = float(result.get("score", 0.0))
                score = raw_score
                score += lexical_relevance_bonus(normalized_query, query_terms, normalized_content, raw_query=query)
                score += memory_type_adjustment(mem_type)
                metadata = result.get("metadata", {})
                score += source_quality_adjustment(normalized_content, metadata)
                score += query_intent_adjustment(normalized_query, mem_type, normalized_content)

                result["score"] = score
                result["raw_score"] = raw_score
                result["memory_type"] = mem_type
                results.append(result)

    # Search ingested content via SQLite store
    if memory_type is None or memory_type == "ingested":
        sqlite_store = _get_sqlite_store()
        if sqlite_store is not None:
            sqlite_results = sqlite_store.search(
                query_embedding=query_embedding,
                limit=per_type_limit,
            )
            for result in sqlite_results:
                content = result.get("content", "")
                normalized_content = normalize_text(content)

                if should_exclude_result(normalized_content):
                    continue

                metadata = result.get("metadata", {})
                raw_score = float(result.get("score", 0.0))
                score = raw_score
                score += lexical_relevance_bonus(normalized_query, query_terms, normalized_content, raw_query=query)
                score += memory_type_adjustment("ingested")
                score += source_quality_adjustment(normalized_content, metadata)
                score += query_intent_adjustment(normalized_query, "ingested", normalized_content)

                result["score"] = score
                result["raw_score"] = raw_score
                result["memory_type"] = "ingested"
                results.append(result)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


# Proper-noun (entity) detection. A capitalized token of length ≥ 3 that is
# NOT at the start of the message is treated as a named entity (pet name,
# person name, place name). When such a name appears in a record's content,
# the record gets a strong relevance boost so name-match wins over richer
# but name-mismatched embedding similarity. Fix 3 (2026-04-27).
_ENTITY_NAME_RE = re.compile(r"\b[A-Z][a-z]{2,}\b")
_ENTITY_NAME_BOOST = 0.20
_ENTITY_NAME_CAP = 0.40

# Capitalized words that are NOT names — common pronouns, English first
# words, and kinship nouns already handled by the possessive-marker logic
# in src/context/policies.py. Filtering these out prevents overboost on
# routine sentence-start words ("My dog Buddy" — "My" is sentence-start;
# "Buddy" is the actual name).
_ENTITY_NAME_BLOCKLIST: frozenset[str] = frozenset({
    "I", "I'm", "I've", "I'll", "I'd",
    "What", "When", "Where", "Why", "How", "Who", "Which",
    "The", "This", "That", "These", "Those",
    "My", "Your", "His", "Her", "Their", "Our",
    # Common content-prefix words that surface in queries but aren't
    # names. Listed so a record won't over-rank just because a query
    # opens with one of these capitalized.
    "Tell", "Show", "Find", "Look", "Search", "Recall",
    # Kinship nouns — already covered by the policies.py possessive guard;
    # treating them as names would double-boost.
    "Son", "Daughter", "Mother", "Father", "Brother", "Sister",
    "Wife", "Husband", "Partner", "Friend", "Family",
    "Dog", "Cat", "Pet", "Bird", "Fish", "Horse", "Animal",
})


def _extract_entity_names(raw_query: str) -> list[str]:
    """Return capitalized tokens from raw_query that are likely proper nouns.

    Skips the first word of the message (sentence-initial capitalization is
    not a name signal) and filters out common pronouns / question words /
    kinship nouns. Returns lowercased names ready for substring matching
    against normalized_content.
    """
    if not raw_query:
        return []

    matches = list(_ENTITY_NAME_RE.finditer(raw_query))
    if not matches:
        return []

    # Skip the first match if it starts at position 0 or only-whitespace
    # precedes it — sentence-initial capitalization is not a name signal.
    names: list[str] = []
    for m in matches:
        token = m.group()
        if token in _ENTITY_NAME_BLOCKLIST:
            continue
        # Determine whether this match is at a sentence boundary (start of
        # message, or after a sentence-terminator + whitespace). If so,
        # capitalization is mandatory and not a name signal.
        prefix = raw_query[: m.start()]
        if not prefix.strip():
            continue
        # Check if the token follows a sentence terminator
        prior = prefix.rstrip()
        if prior and prior[-1] in ".!?":
            continue
        names.append(token.lower())
    return names


def lexical_relevance_bonus(
    normalized_query: str,
    query_terms: list[str],
    normalized_content: str,
    raw_query: str | None = None,
) -> float:
    bonus = 0.0

    if normalized_query and normalized_query in normalized_content:
        bonus += 0.10

    term_hits = sum(1 for term in query_terms if term in normalized_content)
    bonus += min(term_hits * 0.03, 0.18)

    # Fix 3: named-entity discriminator. Each proper noun in the query that
    # appears verbatim in the record content adds _ENTITY_NAME_BOOST to the
    # record's score, capped at _ENTITY_NAME_CAP across all entity matches.
    # Strong enough to overcome typical embedding-cosine variance (0.3-0.5)
    # so the record about "Balor" surfaces above semantically similar but
    # name-mismatched records about other entities.
    if raw_query:
        entity_names = _extract_entity_names(raw_query)
        if entity_names:
            entity_bonus = 0.0
            for name in entity_names:
                if name in normalized_content:
                    entity_bonus += _ENTITY_NAME_BOOST
            bonus += min(entity_bonus, _ENTITY_NAME_CAP)

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


def source_quality_adjustment(content: str, metadata: dict | None = None) -> float:
    score = 0.0
    metadata = metadata or {}

    role = metadata.get("role", "")
    if role == "user":
        score += 0.16
    elif role == "assistant":
        score -= 0.20
    elif content.startswith("user:"):
        score += 0.16
    elif content.startswith("assistant:"):
        score -= 0.20

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
            # Reduced from -0.08. The original penalty assumed
            # ingested content is generic reference material. ChatGPT exports
            # contain first-person work dialogue that's relevant to reflective
            # queries. -0.03 still deprioritizes vs conversation/reflection
            # but doesn't suppress entirely.
            score -= 0.03

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
        "here's a summary",
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
