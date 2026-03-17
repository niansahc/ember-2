import re

from src.context.formatter import ContextFormatter
from src.context.models import ContextPacket
from src.context.ranker import ContextRanker
from src.context.retriever import ContextRetriever


class ContextService:
    def __init__(
        self,
        retriever: ContextRetriever | None = None,
        ranker: ContextRanker | None = None,
        formatter: ContextFormatter | None = None,
    ) -> None:
        self.retriever = retriever or ContextRetriever()
        self.ranker = ranker or ContextRanker()
        self.formatter = formatter or ContextFormatter()

    def build_context(self, user_message: str) -> ContextPacket:
        memory_items, reflection_items = self.retriever.retrieve(user_message)

        ranked_memory, ranked_reflections = self.ranker.rank(
            memory_items, reflection_items
        )

        query_terms = self._extract_query_terms(user_message)

        relevant_memory = [
            item for item in ranked_memory if self._relevance_hits(item, query_terms) > 0
        ]

        if not relevant_memory:
            relevant_memory = ranked_memory

        deduped_memory = self._deduplicate(relevant_memory)

        selected_memory = self._select_diverse_memory(deduped_memory, limit=6)
        selected_reflections = self._deduplicate(ranked_reflections)[:2]

        return self.formatter.format(
            user_message=user_message,
            memory_items=selected_memory,
            reflection_items=selected_reflections,
        )

    def _extract_query_terms(self, user_message: str) -> list[str]:
        terms = re.findall(r"\b[a-z0-9]{3,}\b", user_message.lower())
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
        }
        return [term for term in terms if term not in stopwords]

    def _relevance_hits(self, item, query_terms: list[str]) -> int:
        content = item.content.lower()
        return sum(1 for term in query_terms if term in content)

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
            "conversation": [item for item in items if item.item_type == "conversation"],
            "ingested": [item for item in items if item.item_type == "ingested"],
            "other": [
                item
                for item in items
                if item.item_type not in {"conversation", "ingested"}
            ],
        }

        selected = []

        while len(selected) < limit:
            made_progress = False

            for group_name in ("conversation", "ingested", "other"):
                candidate = self._best_diverse_candidate(
                    grouped_items[group_name], selected
                )

                if candidate is not None:
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
                if candidate is None:
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
        relevance = 1.0

        candidate_tokens = self._tokenize(candidate.content)
        candidate_type = getattr(candidate, "item_type", "unknown")

        max_similarity = 0.0
        same_type_penalty = 0.0

        for existing in selected:
            existing_tokens = self._tokenize(existing.content)
            similarity = self._jaccard_similarity(candidate_tokens, existing_tokens)
            max_similarity = max(max_similarity, similarity)

            if getattr(existing, "item_type", "unknown") == candidate_type:
                same_type_penalty += 0.08

        return relevance - (max_similarity * 0.6) - same_type_penalty

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