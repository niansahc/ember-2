from __future__ import annotations

import re
from datetime import datetime, timezone

from src.context.models import ContextItem
from src.state.models import StateItem


class ContextRanker:
    """Applies policy-based scoring adjustments and ranks context items.

    All scoring constants in this class were tuned empirically against
    the retrieval eval (tools/eval_retrieval.py, 15 benchmark cases) and
    manual conversation testing. They are not arbitrary — each addresses
    a specific failure mode observed during development. The constants
    are documented inline so future tuning can understand the rationale
    before adjusting values.
    """

    def apply_policy(self, items: list[ContextItem], policy) -> list[ContextItem]:
        adjusted: list[ContextItem] = []

        for item in items:
            score = float(item.score)

            if item.item_type == "reflection":
                score *= policy.reflection_weight
            else:
                score *= policy.memory_weight

            if getattr(policy, "recency_bias", 0.0):
                score += self._recency_boost(item.timestamp) * float(policy.recency_bias)

            content = item.content.lower()
            metadata = getattr(item, "metadata", {}) or {}
            content_kind = metadata.get("content_kind")

            if getattr(policy, "prefer_experiences", False):
                if content_kind == "experience" or self._looks_like_experience(content):
                    # +0.20: concrete first-person experiences ("I was", "I felt")
                    # are more valuable than third-person summaries for reflective
                    # queries. Tuned to be significant but not overwhelming — a
                    # high-similarity non-experience can still win.
                    score += 0.20

            if getattr(policy, "prefer_active_work", False):
                if self._looks_like_active_work(content, metadata):
                    # +0.22: slightly above experience boost because work/task
                    # queries need current project context to be useful. A stale
                    # experience from weeks ago is less relevant than today's
                    # work log for "what am I working on" queries.
                    score += 0.22

            if getattr(policy, "prefer_exact_matches", False):
                queryish_bonus = 0.0
                if content_kind == "question":
                    # -0.05: questions as retrieved context are usually the user's
                    # own prior question, not useful evidence. Mild penalty.
                    queryish_bonus -= 0.05
                else:
                    queryish_bonus += 0.03
                score += queryish_bonus

            # ADR-015: Tier scoring modifier.
            # Profile bypasses tier scoring entirely.
            tier = getattr(item, "tier", "hot") or "hot"
            mem_type = getattr(item, "memory_type", "")

            if mem_type == "profile":
                pass  # profile bypasses tier scoring
            elif tier == "cold":
                # Cold items are suppressed entirely — they scored below the
                # warm threshold in the tiering service and should not compete
                # for context slots.
                score = 0.0
            elif tier == "warm":
                # 0.7 multiplier: warm items are retained but disadvantaged.
                # They represent content that was once relevant but has not
                # been retrieved recently. The 30% penalty is enough to push
                # them below hot items of similar base score but still allows
                # them to surface when nothing better exists.
                score *= 0.7
            # hot: no change

            item.score = score
            adjusted.append(item)

        return adjusted

    def apply_state_boost(
        self,
        state_items: list[StateItem],
        policy,
    ) -> list[StateItem]:
        """
        Apply policy state_boost to state items.

        For status_state queries (state_boost > 0), state items are
        already the primary source of truth — this method adds a score
        attribute to StateItem objects so they can be prioritized in
        context assembly.

        StateItem has no score field by default — we attach one via
        a simple wrapper approach: return items sorted by priority
        (high > medium > low > None) when state_boost > 0,
        otherwise return as-is.
        """
        boost = getattr(policy, "state_boost", 0.0)

        if not state_items or boost == 0.0:
            return state_items

        priority_order = {"high": 3, "medium": 2, "low": 1}

        return sorted(
            state_items,
            key=lambda item: priority_order.get(item.priority or "", 0),
            reverse=True,
        )

    def apply_project_boost(
        self,
        items: list[ContextItem],
        project_id: str | None,
    ) -> list[ContextItem]:
        """
        Boost memories that belong to the active project (ADR-007).

        This is a boost, not a filter — all items are returned, but items
        whose metadata.project_id matches the active project get a score
        increase of 0.15. This is meaningful enough to promote project-relevant
        memories without overwhelming general recall.

        If project_id is None (no active project), items are returned unchanged.
        """
        if not project_id or not items:
            return items

        for item in items:
            metadata = getattr(item, "metadata", {}) or {}
            if metadata.get("project_id") == project_id:
                item.score = float(item.score) + 0.15

        return items

    def rank(
        self,
        memory_items: list[ContextItem],
        reflection_items: list[ContextItem],
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        ranked_memory = [self._score_memory_item(item) for item in memory_items]
        ranked_reflections = [self._score_reflection_item(item) for item in reflection_items]

        ranked_memory.sort(key=lambda item: item.score, reverse=True)
        ranked_reflections.sort(key=lambda item: item.score, reverse=True)

        return ranked_memory, ranked_reflections

    def _score_memory_item(self, item: ContextItem) -> ContextItem:
        """Score a memory item for ranking. All constants are empirical.

        The scoring hierarchy encodes a clear priority order:
          1. User-authored first-person experiences (highest)
          2. User conversation turns
          3. Reflections and summaries
          4. Ingested third-party content
          5. Assistant responses (penalized — self-echo risk)
          6. Tool/system traces (heavily penalized)
        """
        score = float(item.score)

        item_type = getattr(item, "item_type", "")
        metadata = getattr(item, "metadata", {}) or {}
        content = item.content.lower().strip()

        # Type boost: conversation > reflection > generic memory > ingested.
        # Conversation content is the user's own words and the most reliable
        # evidence of their actual experience. Ingested content (imported
        # docs, chat exports) gets no boost — it competes on semantic
        # similarity alone.
        if item_type == "conversation":
            score += 0.10
        elif item_type == "reflection":
            score += 0.06
        elif item_type == "memory":
            score += 0.04
        elif item_type == "ingested":
            score += 0.00

        role = metadata.get("role")
        content_kind = metadata.get("content_kind")

        # Role scoring: user content is evidence; assistant content is echo risk.
        # +0.12 user: the user's own words are the strongest evidence of their
        # experience, values, and decisions. Boosting user-authored content is
        # the single most effective retrieval quality lever.
        # -0.25 assistant: assistant self-echo is the #1 context quality issue.
        # Prior assistant responses retrieved and presented unlabeled cause
        # the model to attribute its own words back to the user. This penalty
        # was tuned to be strong enough that assistant content almost never
        # wins a slot unless nothing else is available.
        # -0.20 tool/system: traces, metadata, and system messages are noise.
        if role == "user":
            score += 0.12
        elif role == "assistant":
            score -= 0.25
        elif role in {"tool", "system"}:
            score -= 0.20

        # Content kind scoring: experiences > user_content > questions/answers.
        # +0.14 experience: concrete first-person accounts ("I tried X and Y
        # happened") are the most valuable retrieval content for reflective
        # and identity queries.
        # -0.10 answer: assistant answers get an additional penalty beyond
        # the role penalty — they are the most common source of self-echo.
        # -0.10 question: user questions are usually context-setting, not
        # evidence. The actual content is in the answer or follow-up.
        if content_kind == "experience":
            score += 0.14
        elif content_kind == "user_content":
            score += 0.05
        elif content_kind == "answer":
            score -= 0.10
        elif content_kind == "question":
            score -= 0.10

        if content.startswith("user:"):
            score += 0.04

        # Length scoring: very short content is usually noise (greetings, "yes",
        # "ok"), very long content is usually a full document dump that dilutes
        # the context packet. The sweet spot is 50-1200 chars.
        if len(content) < 20:
            score -= 0.10
        elif len(content) < 50:
            score -= 0.04
        elif len(content) > 1200:
            score -= 0.03

        token_count = len(self._tokenize(content))
        if token_count < 5:
            score -= 0.05

        # -0.18 low-value prompts: specific phrases that surfaced repeatedly
        # in retrieval during testing and added no useful context. These are
        # meta-questions that the user asked Ember, not substantive content.
        if self._looks_like_low_value_prompt(content):
            score -= 0.18

        score += self._recency_boost(item.timestamp)

        item.score = score
        return item

    def _score_reflection_item(self, item: ContextItem) -> ContextItem:
        """Score a reflection item. Reflections get a slight base discount
        (0.95x) because they are derived artifacts — the source memory
        they summarize is usually more specific and more useful. Short
        reflections (<30 chars) are likely junk from failed synthesis.
        Recency boost is halved (0.5x) because reflections cover time
        windows, not moments — a weekly reflection from 10 days ago is
        still relevant in a way that a conversation turn from 10 days
        ago is not.
        """
        score = float(item.score)
        content = item.content.lower().strip()

        score *= 0.95

        if len(content) < 30:
            score -= 0.08

        score += self._recency_boost(item.timestamp) * 0.5

        item.score = score
        return item

    def _recency_boost(self, timestamp: str | None) -> float:
        """Time-based scoring adjustment. Recent content is more likely to
        be relevant to the user's current context.

        Buckets were chosen to match natural conversation rhythms:
          ≤7 days  (+0.18): this week's content is almost certainly relevant
          ≤30 days (+0.12): this month — still fresh, still useful
          ≤90 days (+0.06): this quarter — relevant for patterns and projects
          ≤1 year  (+0.02): mild boost, enough to break ties
          >1 year  (-0.03): slight penalty, old content needs high semantic
                            similarity to justify a context slot

        The boost magnitudes are calibrated against the type and role boosts
        above — a 7-day-old assistant response (+0.18 recency - 0.25 role =
        -0.07) still scores below a 30-day-old user experience (+0.12
        recency + 0.12 role + 0.14 experience = +0.38). This is intentional:
        recency should never override source quality.
        """
        if not timestamp:
            return 0.0

        try:
            ts = float(timestamp)
            item_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (TypeError, ValueError):
            try:
                normalized = timestamp.replace("Z", "+00:00")
                item_dt = datetime.fromisoformat(normalized)
                if item_dt.tzinfo is None:
                    item_dt = item_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                return 0.0

        now = datetime.now(timezone.utc)
        age_days = max((now - item_dt).days, 0)

        if age_days <= 7:
            return 0.18
        if age_days <= 30:
            return 0.12
        if age_days <= 90:
            return 0.06
        if age_days <= 365:
            return 0.02
        return -0.03

    def _looks_like_low_value_prompt(self, content: str) -> bool:
        markers = (
            "what have i been working on today?",
            "yes, tell me all the things you see",
            "do you think i am doing okay or struggling?",
        )
        return any(marker in content for marker in markers)

    def _looks_like_experience(self, content: str) -> bool:
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
            "noticed",
            "experiencing",
            "having",
            "trying",
        )
        return any(marker in content for marker in markers)

    def _looks_like_active_work(self, content: str, metadata: dict) -> bool:
        title = str(metadata.get("title", "")).lower()

        markers = (
            "working on",
            "trying to",
            "focused on",
            "making progress",
            "next step",
            "next steps",
            "plan",
            "planning",
            "started",
            "finished",
            "need to",
            "figuring out",
            "stuck",
            "blocked",
            "updating",
            "changing",
            "organizing",
            "building",
            "improving",
            "fixing",
        )

        return any(marker in content for marker in markers) or any(
            marker in title for marker in markers
        )

    def _tokenize(self, text: str) -> list[str]:
        return re.findall(r"\b[a-z0-9]{3,}\b", text)
