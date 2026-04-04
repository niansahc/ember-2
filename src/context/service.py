import re

from src.context.formatter import ContextFormatter
from src.context.models import ContextPacket
from src.context.policies import classify_query
from src.context.ranker import ContextRanker
from src.context.retriever import ContextRetriever
from src.tools.web_search import web_search


class ContextService:
    def __init__(
        self,
        retriever: ContextRetriever | None = None,
        ranker: ContextRanker | None = None,
        formatter: ContextFormatter | None = None,
        debug: bool = False,
    ) -> None:
        self.retriever = retriever or ContextRetriever()
        self.ranker = ranker or ContextRanker()
        self.formatter = formatter or ContextFormatter()
        self.debug = debug

    def build_context(
        self,
        user_message: str,
        image_data: list[str] | None = None,
        project_id: str | None = None,
    ) -> ContextPacket:
        policy = classify_query(user_message)

        web_items: list[dict] = []
        if policy.use_web_search:
            web_items = web_search(user_message)

        state_items, task_items, memory_items, reflection_items = self.retriever.retrieve(user_message)
        state_items = self.ranker.apply_state_boost(state_items, policy)

        # Relevance gate for default policy: if no non-profile items have
        # raw cosine similarity >= threshold, suppress vault memory entirely.
        # Prevents general knowledge queries from getting vault-based coaching.
        # Profile items are exempt — identity queries should always surface.
        if policy.name == "default":
            from src.core.config import get_retrieval_min_raw_score
            min_raw = get_retrieval_min_raw_score()
            non_profile = [i for i in memory_items if getattr(i, "memory_type", "") != "profile"]
            max_raw = max(
                (getattr(i, "metadata", {}).get("raw_score", 0.0) for i in non_profile),
                default=0.0,
            )
            if max_raw < min_raw:
                # Keep only profile items — suppress everything else
                memory_items = [i for i in memory_items if getattr(i, "memory_type", "") == "profile"]
                reflection_items = []

        # ADR-018: Apply intent-aware type gating before ranking.
        # Profile items bypass type gating — identity context is never suppressed.
        memory_items = self._apply_type_gate(memory_items, policy)
        reflection_items = self._apply_type_gate(reflection_items, policy)

        memory_items = self.ranker.apply_policy(memory_items, policy)
        reflection_items = self.ranker.apply_policy(reflection_items, policy)

        # Boost memories from the active project (ADR-007)
        memory_items = self.ranker.apply_project_boost(memory_items, project_id)
        reflection_items = self.ranker.apply_project_boost(reflection_items, project_id)

        ranked_memory, ranked_reflections = self.ranker.rank(
            memory_items, reflection_items
        )

        normalized_user_message = self._normalize_text(user_message)

        # Filter echo/meta/low-value content directly from ranked results.
        # _relevance_hits was removed — it dropped semantically correct results
        # that used synonyms or related terms (e.g. "work" for query "working").
        # The vector search + ranker already handle relevance ranking.
        filtered_memory = [
            item
            for item in ranked_memory
            if not self._is_echo_or_meta_memory(item, normalized_user_message)
            and not self._is_low_value_memory(item)
        ]

        if not filtered_memory:
            filtered_memory = ranked_memory

        deduped_memory = self._deduplicate(filtered_memory)
        deduped_reflections = self._deduplicate(ranked_reflections)

        memory_limit = self._memory_limit_for_policy(policy.name)
        reflection_limit = self._reflection_limit_for_policy(policy.name)

        # Profile items are guaranteed slots — partition them out first so the
        # ranker's score-based ordering cannot push them below the limit cutoff.
        profile_items = [i for i in deduped_memory if i.memory_type == "profile"]
        other_items = [i for i in deduped_memory if i.memory_type != "profile"]
        remaining_limit = max(0, memory_limit - len(profile_items))

        if policy.diversity:
            selected_other = self._select_diverse_memory(
                other_items,
                limit=remaining_limit,
            )
        else:
            selected_other = other_items[:remaining_limit]

        selected_memory = profile_items + selected_other

        selected_reflections = deduped_reflections[:reflection_limit]

        if self.debug:
            for item in selected_memory:
                print(f"[CTX] {item.item_type}: {item.content[:120]}")

        # ADR-015: Update retrieval stats on selected records only.
        # Only records that made it into the final context packet get
        # their retrieval_count incremented and last_retrieved_at set.
        self._update_retrieval_stats(selected_memory + selected_reflections)

        return self.formatter.format(
            user_message=user_message,
            memory_items=selected_memory,
            reflection_items=selected_reflections,
            state_items=state_items,
            task_items=task_items,
            web_items=web_items,
            image_data=image_data or [],
        )

    def _update_retrieval_stats(self, items: list) -> None:
        """
        ADR-015: Update retrieval_count and last_retrieved_at for selected records.

        Only called on records that made it into the final context packet.
        Runs in a try/except so retrieval stat failures never crash context building.
        """
        try:
            from src.retrieval.semantic_search import _get_memory_store, _get_sqlite_store

            # Collect record IDs by store
            memory_ids = []
            ingested_ids = []

            for item in items:
                record_id = getattr(item, "id", "")
                mem_type = getattr(item, "memory_type", "")
                if not record_id:
                    continue
                if mem_type == "ingested":
                    ingested_ids.append(record_id)
                elif mem_type in {"conversation", "profile", "reflection", "journal"}:
                    memory_ids.append(record_id)

            memory_store = _get_memory_store()
            if memory_store and memory_ids:
                memory_store.update_retrieval_stats(memory_ids)

            sqlite_store = _get_sqlite_store()
            if sqlite_store and ingested_ids:
                sqlite_store.update_retrieval_stats(ingested_ids)

        except Exception:
            pass  # retrieval stats are best-effort, never crash context building

    def _apply_type_gate(self, items: list, policy) -> list:
        """
        ADR-018: Filter memory items by eligible/suppressed types and min_score.

        Applied before ranking so ineligible candidates never compete for slots.
        Profile items bypass type gating — identity context is never suppressed.
        """
        filtered = items

        if policy.suppress_memory_types:
            filtered = [
                i for i in filtered
                if getattr(i, "memory_type", None) not in policy.suppress_memory_types
                or getattr(i, "memory_type", None) == "profile"
            ]

        if policy.eligible_memory_types is not None:
            filtered = [
                i for i in filtered
                if getattr(i, "memory_type", None) in policy.eligible_memory_types
                or getattr(i, "memory_type", None) == "profile"
            ]

        filtered = [
            i for i in filtered
            if getattr(i, "score", 0.0) >= policy.min_score
            or getattr(i, "memory_type", None) == "profile"
        ]

        return filtered

    def _memory_limit_for_policy(self, policy_name: str) -> int:
        if policy_name == "reflective":
            return 4
        if policy_name == "recent_activity":
            return 6
        if policy_name == "recent":
            return 5
        if policy_name == "activity":
            return 6
        if policy_name == "factual_recall":
            return 6
        return 6

    def _reflection_limit_for_policy(self, policy_name: str) -> int:
        if policy_name == "reflective":
            return 3
        if policy_name == "recent_activity":
            return 2
        if policy_name == "recent":
            return 2
        if policy_name == "activity":
            return 1
        if policy_name == "factual_recall":
            return 1
        return 2

    def _is_echo_or_meta_memory(self, item, normalized_user_message: str) -> bool:
        content = self._normalize_text(item.content)

        if not content:
            return True

        meta_markers = (
            "user asked:",
            "ember responded:",
            "assistant responded:",
            "assistant said:",
            "question:",
            "answer:",
        )

        if any(marker in content for marker in meta_markers):
            return True

        if normalized_user_message and normalized_user_message in content:
            return True

        similarity = self._jaccard_similarity(
            self._tokenize(content),
            self._tokenize(normalized_user_message),
        )

        return similarity > 0.55

    def _is_low_value_memory(self, item) -> bool:
        content = self._normalize_text(item.content)
        metadata = getattr(item, "metadata", {}) or {}

        if len(content) < 40:
            return True

        low_value_exact = (
            "user: yes, tell me all the things you see",
            "user: what have i been working on today?",
        )
        if content in low_value_exact:
            return True

        low_value_markers = (
            "as an ai, i don't have personal experiences or memories",
            "tell me all the things you see",
            "do you think i am doing okay or struggling",
            "shorter responses",
            "shorter messages please",
            "that's a long response again",
        )
        if any(marker in content for marker in low_value_markers):
            return True

        if metadata.get("content_kind") == "question" and len(content) < 120:
            return True

        return False

    def _deduplicate(self, items: list) -> list:
        seen = set()
        deduped = []

        for item in items:
            key = self._normalize_text(item.content)
            if key not in seen:
                deduped.append(item)
                seen.add(key)

        return deduped

    def _select_diverse_memory(self, items: list, limit: int) -> list:
        if not items:
            return []

        grouped_items = {
            "conversation": [i for i in items if i.item_type == "conversation"],
            "ingested": [i for i in items if i.item_type == "ingested"],
            "other": [i for i in items if i.item_type not in {"conversation", "ingested"}],
        }

        selected = []

        while len(selected) < limit:
            made_progress = False

            for group_name in ("conversation", "ingested", "other"):
                candidate = self._best_diverse_candidate(
                    grouped_items[group_name], selected
                )

                if candidate:
                    selected.append(candidate)
                    grouped_items[group_name].remove(candidate)
                    made_progress = True

                    if len(selected) >= limit:
                        break

            if not made_progress:
                break

        if len(selected) < limit:
            remaining = []
            for group_items in grouped_items.values():
                remaining.extend(group_items)

            while len(selected) < limit and remaining:
                candidate = self._best_diverse_candidate(remaining, selected)
                if not candidate:
                    break
                selected.append(candidate)
                remaining.remove(candidate)

        return selected

    def _best_diverse_candidate(self, candidates: list, selected: list):
        if not candidates:
            return None

        if not selected:
            return candidates[0]

        best_item = None
        best_score = float("-inf")

        for candidate in candidates:
            score = self._diversity_score(candidate, selected)
            if score > best_score:
                best_score = score
                best_item = candidate

        return best_item

    def _diversity_score(self, candidate, selected: list) -> float:
        relevance = float(getattr(candidate, "score", 0.0))

        if len(candidate.content) < 80:
            relevance -= 0.08

        candidate_tokens = self._tokenize(candidate.content)
        candidate_type = getattr(candidate, "item_type", "unknown")
        candidate_metadata = getattr(candidate, "metadata", {}) or {}
        candidate_doc_id = candidate_metadata.get("doc_id")
        candidate_title = candidate_metadata.get("title")

        max_similarity = 0.0
        same_type_penalty = 0.0
        same_doc_penalty = 0.0
        same_title_penalty = 0.0

        for existing in selected:
            existing_tokens = self._tokenize(existing.content)
            similarity = self._jaccard_similarity(candidate_tokens, existing_tokens)
            max_similarity = max(max_similarity, similarity)

            existing_type = getattr(existing, "item_type", "unknown")
            existing_metadata = getattr(existing, "metadata", {}) or {}

            if existing_type == candidate_type:
                same_type_penalty += 0.05

            if candidate_doc_id and existing_metadata.get("doc_id") == candidate_doc_id:
                same_doc_penalty += 0.22

            if candidate_title and existing_metadata.get("title") == candidate_title:
                same_title_penalty += 0.08

        return (
            relevance
            - (max_similarity * 0.70)
            - same_type_penalty
            - same_doc_penalty
            - same_title_penalty
        )

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
