"""
src/context/retriever.py

ContextRetriever gathers raw candidate items from the vault's vector
indexes and state layer. It does not rank or filter — that is the
ranker's and service's job. It handles identity query detection,
profile retrieval routing, and the semantic search calls across
multiple index stores (memory.db, ingested.db, conversation, reflection).
"""

import re
import warnings

from src.context.models import ContextItem
from src.memory.search_conversation import search_conversation_memories
from src.memory.service import MemoryService
from src.retrieval.semantic_search import semantic_search as _semantic_search
from src.state.models import StateItem
from src.state.state_resolver import StateResolver
from src.tasks.models import TaskItem
from src.tasks.task_resolver import TaskResolver


class ContextRetriever:
    def __init__(
        self,
        memory_service: MemoryService | None = None,
        state_resolver: StateResolver | None = None,
        task_resolver: TaskResolver | None = None,
    ):
        self.memory_service = memory_service or MemoryService()
        # StateResolver is injected so tests can pass a resolver backed by a
        # temp vault directory without touching the real private vault.
        self.state_resolver = state_resolver or StateResolver()
        self.task_resolver = task_resolver or TaskResolver()

    def get_state_items(self) -> list[StateItem]:
        """
        Return current state items from the vault via StateResolver.

        Calls StateResolver.get_current_state() which applies "latest record
        wins" per category and returns one StateItem per populated category.

        Failures are caught and logged as warnings — state retrieval must
        never crash context building. An empty list is returned on error so
        the rest of the pipeline continues normally.

        Returns
        -------
        list[StateItem]
            Current state items (one per populated category), or an empty
            list if the vault has no state records or an error occurs.
        """
        try:
            return self.state_resolver.get_current_state()
        except Exception as exc:  # noqa: BLE001
            warnings.warn(
                f"[CONTEXT_RETRIEVER] State retrieval failed, continuing without "
                f"state context: {exc}",
                stacklevel=2,
            )
            return []

    def get_task_items(self) -> list[TaskItem]:
        """
        Return active tasks from the vault via TaskResolver.

        Failures are caught and logged as warnings -- task retrieval must
        never crash context building.

        Returns
        -------
        list[TaskItem]
            Active task items (proposed + active), or an empty list on error.
        """
        try:
            return self.task_resolver.get_active_tasks()
        except Exception as exc:  # noqa: BLE001
            warnings.warn(
                f"[CONTEXT_RETRIEVER] Task retrieval failed, continuing without "
                f"task context: {exc}",
                stacklevel=2,
            )
            return []

    def get_memory_items(
        self,
        user_message: str,
        query_embedding: list[float] | None = None,
    ) -> list[ContextItem]:
        from src.retrieval.semantic_search import semantic_search

        results = semantic_search(user_message, limit=8, query_embedding=query_embedding)
        items: list[ContextItem] = []

        for result in results:
            metadata = result.get("metadata", {})
            content = result.get("content", "")
            mem_type = result.get("memory_type", "memory")

            if self._should_exclude_content(content, user_message):
                continue

            items.append(
                ContextItem(
                    id=metadata.get("chunk_id", result.get("path", "")),
                    content=content,
                    source=mem_type,
                    item_type=mem_type,
                    memory_type=mem_type,
                    score=result.get("score", 0.0),
                    timestamp=metadata.get("created_at"),
                    tags=metadata.get("tags", []),
                    metadata={
                        **metadata,
                        "path": result.get("path"),
                        "memory_type": mem_type,
                        "raw_score": result.get("raw_score", 0.0),
                    },
                    tier=result.get("tier", "hot"),
                    # Propagate authorship from the
                    # SQLite index. Missing column returns "unknown" via
                    # the store's fallback — safe default.
                    authorship=result.get("authorship", "unknown"),
                )
            )

        return items

    def get_reflection_items(self, user_message: str) -> list[ContextItem]:
        results = self.memory_service.search(
            user_message,
            memory_type="reflection",
            limit=3,
        )

        if not results:
            results = self.memory_service.read(memory_type="reflection", limit=1)

        items: list[ContextItem] = []

        for result in results:
            content = result.get("text", "")

            if self._should_exclude_content(content, user_message):
                continue

            items.append(
                ContextItem(
                    id=result.get("id", ""),
                    content=content,
                    source="reflection",
                    item_type="reflection",
                    memory_type="reflection",
                    score=1.0,
                    timestamp=result.get("timestamp"),
                    tags=result.get("tags", []),
                    metadata=result,
                )
            )

        return items

    # Identity query markers — when the user asks about themselves OR about
    # Ember, profile records should surface. Ember knowing who she's talking
    # to is essential context for answering identity questions about herself.
    IDENTITY_MARKERS = (
        # User-directed: "tell me about me"
        "know about me",
        "who am i",
        "about myself",
        "tell me about me",
        "what am i like",
        "what do you know about",
        "describe me",
        "my profile",
        # Ember-directed: "tell me about yourself"
        "about yourself",
        "who are you",
        "what are you",
        "describe yourself",
        "tell me about ember",
        "who is ember",
    )

    def _is_identity_query(self, query: str) -> bool:
        """Check if the query is asking about the user's identity/profile."""
        q = query.lower().strip()
        return any(marker in q for marker in self.IDENTITY_MARKERS)

    def get_profile_items(
        self,
        user_message: str,
        query_embedding: list[float] | None = None,
    ) -> list[ContextItem]:
        is_identity = self._is_identity_query(user_message)
        limit = 8 if is_identity else 3
        min_score = 0.0 if is_identity else 0.3

        results = _semantic_search(
            user_message,
            memory_type="profile",
            limit=limit,
            min_score=min_score,
            query_embedding=query_embedding,
        )

        items: list[ContextItem] = []

        for result in results:
            content = result.get("content", "")
            score = result.get("score", 0.0)

            if not content or len(content.strip()) < 40:
                continue

            items.append(
                ContextItem(
                    id=result.get("id", ""),
                    content=content,
                    source="profile",
                    item_type="profile",
                    memory_type="profile",
                    score=score,
                    timestamp=result.get("timestamp"),
                    tags=result.get("tags", []),
                    metadata=result,
                )
            )

        return items

    def get_conversation_items(self, user_message: str) -> list[ContextItem]:
        results = search_conversation_memories(user_message, top_k=6)
        items: list[ContextItem] = []

        for result in results:
            memory = result.get("memory", {})
            content = memory.get("text", "")

            if self._should_exclude_content(content, user_message):
                continue

            items.append(
                ContextItem(
                    id=memory.get("timestamp", ""),
                    content=content,
                    source="conversation",
                    item_type="conversation",
                    memory_type="conversation",
                    score=result.get("score", 0.0),
                    timestamp=memory.get("timestamp"),
                    tags=memory.get("tags", []),
                    metadata=memory,
                )
            )

        return items

    def retrieve(
        self, user_message: str
    ) -> tuple[list[StateItem], list[TaskItem], list[ContextItem], list[ContextItem], list[float] | None]:
        """
        Retrieve all context for a user message.

        Returns a 5-tuple:
          (state_items, task_items, memory_items, reflection_items, query_embedding)

        State and task items come first -- they represent current operational
        truth and should be injected into the prompt before reflections and
        memories.

        Parameters
        ----------
        user_message : str
            The incoming user query used to drive semantic and keyword search.

        Returns
        -------
        tuple[list[StateItem], list[TaskItem], list[ContextItem], list[ContextItem], list[float] | None]
            (state_items, task_items, memory_items, reflection_items, query_embedding)
        """
        state_items = self.get_state_items()
        task_items = self.get_task_items()

        # Compute the query embedding once and reuse across all semantic
        # search paths. Before this optimization, each of get_profile_items,
        # get_memory_items, and the lodestone resolver independently called
        # embed_text(user_message) — 3 identical Ollama calls at ~50-150ms
        # each. Computing once saves 100-300ms per request.
        try:
            from src.retrieval.embed_memory import embed_text
            query_embedding = embed_text(user_message)
        except Exception:
            query_embedding = None

        profile_items = self.get_profile_items(user_message, query_embedding=query_embedding)
        # get_memory_items() does a full semantic_search() which already searches
        # the conversation index. get_conversation_items() would load and search
        # the same conversation index again via search_conversation_memories().
        # Skipping get_conversation_items() to avoid the double index load --
        # conversation results are already included in get_memory_items().
        memory_items = self.get_memory_items(user_message, query_embedding=query_embedding)
        reflection_items = self.get_reflection_items(user_message)

        memory_items = profile_items + memory_items
        memory_items = self._deduplicate_items(memory_items)

        return state_items, task_items, memory_items, reflection_items, query_embedding

    def _deduplicate_items(self, items: list[ContextItem]) -> list[ContextItem]:
        seen = set()
        deduped: list[ContextItem] = []

        for item in items:
            key = self._normalize_text(item.content)
            if not key:
                continue
            if key in seen:
                continue

            deduped.append(item)
            seen.add(key)

        return deduped

    def _should_exclude_content(self, content: str, user_message: str) -> bool:
        normalized_content = self._normalize_text(content)
        normalized_user_message = self._normalize_text(user_message)

        if not normalized_content:
            return True

        if len(normalized_content) < 40:
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

        if any(marker in normalized_content for marker in meta_markers):
            return True

        if normalized_content.startswith("{") or normalized_content.startswith("["):
            return True

        if "```" in content:
            return True

        # File trees and directory listings (Unicode box-drawing characters)
        if "\u2502" in content or "\u251c" in content or "\u2514" in content:
            return True

        # "Recent themes:" followed by short user complaints — session summary junk
        if normalized_content.startswith("recent themes:"):
            return True

        if normalized_user_message and normalized_user_message in normalized_content:
            return True

        similarity = self._jaccard_similarity(
            self._tokenize(normalized_content),
            self._tokenize(normalized_user_message),
        )

        if similarity > 0.60:
            return True

        return False

    def _tokenize(self, text: str) -> set[str]:
        return set(re.findall(r"\b[a-z0-9]{3,}\b", text.lower()))

    def _jaccard_similarity(self, a: set[str], b: set[str]) -> float:
        if not a or not b:
            return 0.0

        union = a | b
        if not union:
            return 0.0

        return len(a & b) / len(union)

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())
