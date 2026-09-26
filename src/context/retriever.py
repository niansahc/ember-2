"""
src/context/retriever.py

ContextRetriever gathers raw candidate items from the vault's vector
indexes and state layer. It does not rank or filter — that is the
ranker's and service's job. It handles identity query detection,
profile retrieval routing, and the semantic search calls across
multiple index stores (memory.db, ingested.db, conversation, reflection).
"""

import logging
import re
import warnings

from src.context.models import ContextItem
from src.memory.service import MemoryService
from src.observability.guard_counters import count
from src.retrieval.semantic_search import semantic_search as _semantic_search
from src.state.models import StateItem
from src.state.state_resolver import StateResolver, _state_debug_enabled
from src.tasks.models import TaskItem
from src.tasks.task_resolver import TaskResolver

logger = logging.getLogger("ember.context_retriever")

# Floor on RAW COSINE for the main memory search, applied inside
# semantic_search before any adjustment (#205).
#
# Until now the main path passed nothing and semantic_search defaults
# min_score to None, so `min_score is not None and raw < min_score` was
# identically false: 0 firings in 4,248 evaluations on
# vector_index.min_score_floor.json and 0 in 2,609 on
# semantic_search.min_score_floor.memory_all_types. The floor existed,
# was configured, and did not run. It fired only through the profile
# search, which does pass one, at 2 of 440.
#
# THE VALUE IS NOT CALIBRATED. 0.25 is inherited from policy.min_score,
# which is a different gate on a different quantity -- that one judges
# the adjusted score after the additive terms, this one judges raw
# cosine. On the production corpus it excludes nothing: 288 candidates
# over 36 queries span 0.4214 to 0.7595, so the nearest value that would
# change delivery is 0.50 (4.5% excluded). Activating the guard and
# choosing its value are separate pieces of work and only the first is
# done here. See the calibration issue before changing this number.
MEMORY_MIN_RAW_SCORE = 0.25


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

        results = semantic_search(
            user_message,
            limit=8,
            query_embedding=query_embedding,
            min_score=MEMORY_MIN_RAW_SCORE,
        )
        items: list[ContextItem] = []

        for result in results:
            metadata = result.get("metadata", {})
            content = result.get("content", "")
            mem_type = result.get("memory_type", "memory")

            if count("retriever.exclude.memory_channel",
                     self._should_exclude_content(content, user_message)):
                continue

            # ADR-021: surface the cached embedding into ContextItem.metadata
            # so the T2 pattern detector can read it without recomputation.
            # Only present on results from the SQLite store (legacy JSON-index
            # results may not carry it). Missing -> detector treats as cache
            # miss and skips the record.
            _embedding = result.get("embedding")
            _enriched_metadata = {
                **metadata,
                "path": result.get("path"),
                "memory_type": mem_type,
                "raw_score": result.get("raw_score", 0.0),
            }
            if _embedding:
                _enriched_metadata["embedding"] = _embedding

            items.append(
                ContextItem(
                    id=metadata.get("chunk_id", result.get("path", "")),
                    content=content,
                    source=mem_type,
                    item_type=mem_type,
                    memory_type=mem_type,
                    score=result.get("score", 0.0),
                    # B-RET-002: created_at lives on the vectors-table
                    # column, not in the JSON metadata blob. Read it from
                    # the row dict directly. Falls back to None when the
                    # field is absent (legacy rows or non-SQLite paths).
                    timestamp=result.get("created_at"),
                    tags=metadata.get("tags", []),
                    metadata=_enriched_metadata,
                    tier=result.get("tier", "hot"),
                    # Propagate authorship from the
                    # SQLite index. Missing column returns "unknown" via
                    # the store's fallback — safe default.
                    authorship=result.get("authorship", "unknown"),
                    # ADR-015 amendment step 4: the real vectors.id, distinct
                    # from `id` above (path/chunk_id, a different identity
                    # contract). Needed so retrieval stats actually update.
                    store_id=result.get("id"),
                )
            )

        return items

    def get_reflection_items(
        self,
        user_message: str,
        query_embedding: list[float] | None = None,
    ) -> list[ContextItem]:
        """Reflection candidates, scored on the same scale as everything else.

        Issue #239. The history here is two corrections deep. First,
        MemoryService.search() is keyword-overlap matching and returns no
        score, so reflections were hardcoded to score=1.0 -- above anything
        a cosine-derived memory item could reach. That was replaced with a
        token Jaccard score against the query, which was comparable in the
        sense of being a number between 0 and 1 and in no other sense: the
        gate that judges it (_apply_type_gate, min_score 0.25) is
        calibrated for cosine, where production top-8 raw spread is 0.0815
        around a mean rank-1 of 0.6375 (#236). Jaccard between a short
        query and a multi-paragraph synthesis does not reach that. Over 36
        queries the channel produced 100 candidates on 34 turns, the best
        scored 0.0794, and the gate rejected every one.

        So the channel is scored by cosine now, through the same
        semantic_search path and the same adjusted score the memory
        channel uses. The gate compares like with like and can
        discriminate rather than reject.

        No "most recent reflection" fallback when the search comes back
        empty -- an empty result means no reflection_items, matching
        get_memory_items() and get_profile_items().
        """
        from src.retrieval.semantic_search import semantic_search

        results = semantic_search(
            user_message,
            memory_type="reflection",
            limit=3,
            query_embedding=query_embedding,
        )

        items: list[ContextItem] = []

        for result in results:
            content = result.get("content", "")

            if count("retriever.exclude.reflection_channel",
                     self._should_exclude_content(content, user_message)):
                continue

            metadata = result.get("metadata", {}) or {}
            items.append(
                ContextItem(
                    id=result.get("id", ""),
                    content=content,
                    source="reflection",
                    item_type="reflection",
                    memory_type="reflection",
                    score=result.get("score", 0.0),
                    # B-RET-002: created_at is a vectors column, not a
                    # metadata key.
                    timestamp=result.get("created_at"),
                    tags=metadata.get("tags", []),
                    metadata={
                        **metadata,
                        "memory_type": "reflection",
                        "raw_score": result.get("raw_score", 0.0),
                    },
                    tier=result.get("tier", "hot"),
                    authorship=result.get("authorship", "unknown"),
                    # ADR-015 amendment step 4: the real vectors.id.
                    store_id=result.get("id"),
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
        # Pets — possessive phrases only so generic animal queries
        # ("best dog breeds") do not falsely trigger profile retrieval.
        "my dog",
        "my cat",
        "my pet",
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
        is_identity = count(
            "profile.identity_query", self._is_identity_query(user_message)
        )
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

            if count("profile.under_40_chars",
                     not content or len(content.strip()) < 40):
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
                    # ADR-015 amendment step 4: profile goes through the
                    # same SQLite semantic_search() path as
                    # get_memory_items(), so it has the same store_id
                    # (vectors.id) vs id (path/chunk_id) mismatch.
                    store_id=result.get("id"),
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

        if _state_debug_enabled():
            top = [
                {
                    "category": getattr(it, "category", None),
                    "timestamp": getattr(it, "timestamp", None),
                }
                for it in state_items[:3]
            ]
            logger.warning(
                "[CONTEXT_RETRIEVER] state_items_count=%d top=%s "
                "user_message_prefix=%r",
                len(state_items),
                top,
                user_message[:80],
            )

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
        # Conversation records live in memory.db (SQLite vector store) alongside
        # other migrated types. get_memory_items() iterates SQLITE_MEMORY_TYPES,
        # which includes "conversation", so conversation results surface here.
        # B-RET-001: the legacy file-based conversation_index.json path is
        # retired -- src/memory/search_conversation.py and the prior
        # get_conversation_items wrapper were dead code that read a stale
        # index never refreshed on writes.
        memory_items = self.get_memory_items(user_message, query_embedding=query_embedding)
        reflection_items = self.get_reflection_items(
            user_message, query_embedding=query_embedding
        )

        memory_items = profile_items + memory_items
        memory_items = self._deduplicate_items(memory_items)
        memory_items = self._drop_reflection_duplicates(
            memory_items, reflection_items
        )

        return state_items, task_items, memory_items, reflection_items, query_embedding

    def _drop_reflection_duplicates(
        self,
        memory_items: list[ContextItem],
        reflection_items: list[ContextItem],
    ) -> list[ContextItem]:
        """Keep a reflection in one channel, not both.

        The memory channel searches every migrated type, reflection
        included, so a record that clears the gate can arrive twice: once
        as a source memory and once as a reflection. Dedup did not catch
        it because it runs per channel. While the reflection channel
        delivered nothing (#239) this could not happen; scoring it by
        cosine makes it reachable.

        Two copies would be rendered in two prompt sections and, since
        #238, recorded as two deliveries of one record.

        The reflection copy wins. It is the one with the
        provenance="derived-synthesis" framing and the per-item age label,
        which exist so the model does not read a synthesis as a source
        record.
        """
        if not reflection_items or not memory_items:
            return memory_items

        store_ids = {
            i.store_id for i in reflection_items if getattr(i, "store_id", None)
        }
        keys = {self._normalize_text(i.content) for i in reflection_items}

        kept: list[ContextItem] = []
        for item in memory_items:
            duplicate = (
                (getattr(item, "store_id", None) or None) in store_ids
                or self._normalize_text(item.content) in keys
            )
            if count("retriever.dedup.reflection_cross_channel", duplicate):
                continue
            kept.append(item)
        return kept

    def _deduplicate_items(self, items: list[ContextItem]) -> list[ContextItem]:
        seen = set()
        deduped: list[ContextItem] = []

        for item in items:
            key = self._normalize_text(item.content)
            if count("retriever.dedup.empty_key", not key):
                continue
            if count("retriever.dedup.duplicate_content", key in seen):
                continue

            deduped.append(item)
            seen.add(key)

        return deduped

    def _should_exclude_content(self, content: str, user_message: str) -> bool:
        """Exclusion rules that semantic_search does not already apply.

        This used to re-check five rules the upstream filter had already
        run: empty, under_40_chars, meta_marker, json_payload and
        code_fence. They were not merely redundant, they were unreachable,
        and the counters showed it -- 0 firings in 391 evaluations each on
        the production corpus while the same-named predicates upstream
        fired 1,152, 229 and 78 times.

        Removed because two copies of one rule drift apart, not because
        they cost anything. The counter evidence alone would not justify
        the deletion; zero firings over one window is equally consistent
        with a rare path. The control-flow argument is what justifies it:

          * every `results.append` in semantic_search is guarded by
            should_exclude_result, on all five branches, so no result
            reaches here without having passed it
          * both call sites of this method consume semantic_search output
            (get_memory_items, get_reflection_items); nothing else calls it
          * the predicates were textually identical, and both sides
            normalize with the same expression, so they ran on the same
            string and could not disagree

        The arms below are the ones with no upstream equivalent, and three
        of the four fire on real traffic.

        Note the dependency this creates. The reflection path is only
        covered because #241 routed it through semantic_search; before
        that it came from MemoryService.search and these five rules were
        load-bearing on it. A new channel that does not go through
        semantic_search needs its own filter, or it needs these back.
        """
        normalized_content = self._normalize_text(content)
        normalized_user_message = self._normalize_text(user_message)

        # File trees and directory listings (Unicode box-drawing characters)
        if count("retriever.exclude.box_drawing",
                 "\u2502" in content or "\u251c" in content or "\u2514" in content):
            return True

        # "Recent themes:" followed by short user complaints -- session summary junk
        if count("retriever.exclude.recent_themes_prefix",
                 normalized_content.startswith("recent themes:")):
            return True

        if count("retriever.exclude.query_verbatim_in_content",
                 bool(normalized_user_message)
                 and normalized_user_message in normalized_content):
            return True

        similarity = self._jaccard_similarity(
            self._tokenize(normalized_content),
            self._tokenize(normalized_user_message),
        )

        if count("retriever.exclude.jaccard_over_0_60",
                 similarity > 0.60):
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
