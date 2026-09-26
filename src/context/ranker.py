"""
src/context/ranker.py

ContextRanker applies policy-based scoring adjustments to retrieved
items and produces the final ranked ordering. Scoring encodes a clear
priority hierarchy: user-authored experiences > conversation turns >
reflections > ingested content > assistant responses. All scoring
constants are empirical and documented inline with rationale.

See the class-level docstring on ContextRanker for the full scoring
philosophy and tuning guidance.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from src.context.models import ContextItem
from src.observability.guard_counters import branch, count, reached
from src.state.models import StateItem

# ADR-015 amendment (PR #180), implementation step 3: cold is a reduced
# weight, not exclusion. Was `score = 0.0` -- collapsing every cold item to
# one indistinguishable value and erasing relative relevance across most of
# the corpus (measured: unresolved tie-band fraction 0.178 with tiering on
# vs 0.015 with it off, roughly 12x -- see the ADR amendment's "Cold is a
# weight, not exclusion" section).
#
# 0.3, not a smaller value: warm's 0.7 discount is a single, moderate step
# down; matching that same ratio again for cold (0.7 * 0.7 = 0.49) would
# leave cold too close to warm and blur the three-tier separation the
# design wants. Going near-zero (0.05-0.1) stays too close to the old
# behavior it replaces and undersells "reduced weight" as a real signal
# rather than a technicality. 0.3 is a clear third band below warm, while
# still leaving enough headroom that a highly relevant cold record can
# outrank a weakly relevant hot one when that is genuinely the better
# answer -- context conditioning and direct addressing are the amendment's
# named recovery paths, not automatic fallback, so ranking has to be able
# to do this on its own sometimes.
COLD_MULTIPLIER = 0.3


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

            if count("ranker.policy.recency_bias_active",
                     bool(getattr(policy, "recency_bias", 0.0))):
                score += self._recency_boost(item.timestamp) * float(policy.recency_bias)

            content = item.content.lower()
            metadata = getattr(item, "metadata", {}) or {}
            content_kind = metadata.get("content_kind")

            if count("ranker.policy.prefer_experiences_enabled",
                     bool(getattr(policy, "prefer_experiences", False))):
                if count("ranker.policy.prefer_experiences_fired",
                         content_kind == "experience"
                         or self._looks_like_experience(content)):
                    # +0.20: concrete first-person experiences ("I was", "I felt")
                    # are more valuable than third-person summaries for reflective
                    # queries. Tuned to be significant but not overwhelming — a
                    # high-similarity non-experience can still win.
                    score += 0.20

            if count("ranker.policy.prefer_active_work_enabled",
                     bool(getattr(policy, "prefer_active_work", False))):
                if count("ranker.policy.prefer_active_work_fired",
                         self._looks_like_active_work(content, metadata)):
                    # +0.22: slightly above experience boost because work/task
                    # queries need current project context to be useful. A stale
                    # experience from weeks ago is less relevant than today's
                    # work log for "what am I working on" queries.
                    score += 0.22

            if count("ranker.policy.prefer_exact_matches_enabled",
                     bool(getattr(policy, "prefer_exact_matches", False))):
                queryish_bonus = 0.0
                if count("ranker.policy.exact_match_question",
                         content_kind == "question"):
                    # -0.05: questions as retrieved context are usually the user's
                    # own prior question, not useful evidence. Mild penalty.
                    queryish_bonus -= 0.05
                else:
                    queryish_bonus += 0.03
                score += queryish_bonus

            # ADR-015: Tier scoring modifier. This is the base-activation
            # half of the amendment's activation model (recency + decaying
            # frequency, computed nightly in TieringService) reaching
            # ranking as a stored tier. The per-query context-conditioning
            # half is apply_project_boost() below -- ADR-007's existing
            # +0.15 project boost, declared the context term rather than a
            # second mechanism added alongside this one.
            # Profile bypasses tier scoring entirely. Structurally
            # unreachable today -- TieringService hard-overrides profile to
            # hot on every nightly run, the SQLite tier column defaults to
            # 'hot', and get_profile_items() never even reads a stored tier
            # into the ContextItem -- but kept explicit anyway. ADR-015's
            # original decision states "Profile memory: always Hot, no
            # exceptions" three separate times; that is worth an explicit,
            # self-documenting guard rather than depending on three other
            # pieces of code never changing.
            tier = getattr(item, "tier", "hot") or "hot"
            mem_type = getattr(item, "memory_type", "")

            if mem_type == "profile":
                branch("ranker.tier", "profile_bypass")
                pass  # profile bypasses tier scoring
            elif tier == "cold":
                branch("ranker.tier", "cold")
                # ADR-015 amendment step 3: reduced weight, not exclusion.
                # Ordering within cold is preserved -- a nonzero multiplier
                # is strictly order-preserving on its own input, applied
                # uniformly here, so two cold items that differ before this
                # line still differ after it. See COLD_MULTIPLIER's module
                # comment for the value rationale.
                score *= COLD_MULTIPLIER
            elif tier == "warm":
                branch("ranker.tier", "warm")
                # 0.7 multiplier: warm items are retained but disadvantaged.
                # They represent content that was once relevant but has not
                # been retrieved recently. The 30% penalty is enough to push
                # them below hot items of similar base score but still allows
                # them to surface when nothing better exists.
                score *= 0.7
            else:
                # hot, or an unrecognised tier falling through to no change.
                branch("ranker.tier", "hot_or_unrecognised")
            # hot: no change

            item.score = score
            adjusted.append(item)

        return adjusted

    def apply_authorship_scoring(
        self,
        items: list[ContextItem],
        user_message: str,
    ) -> list[ContextItem]:
        """Apply authorship multiplier on relational / identity queries.

        When the query is about the user's personal
        relationships or identity ("my son", "my partner", "my health"),
        third-party ingested content (books, articles, other people's
        conversations) must not be allowed to answer as if it were about
        the user. See UAT-005 root cause analysis.

        Multipliers -- applied only when _matches_relational_query is True:
          first_person: 1.0  (user-authored -- conversation/journal/profile)
          mixed:        0.3  (content of uncertain authorship)
          unknown:      0.5  (conservative middle pending re-tag)

        third_party (0.0) was retired in #218. It had zero rows in the
        production index and no live path to acquire any: classify_authorship
        never returns it, and the only code that assigned it was a standalone
        backfill whose ChatGPT rule contradicted ADR-015. See ADR-015's
        2026-09-26 correction. Genuine third-party material -- documents,
        articles, other people's writing -- is owned by the `ingested`
        memory type and ADR-018 type gating, which is a write-time
        provenance fact rather than a rank-time multiplier.

        An unrecognised authorship value now takes the 0.5 default rather
        than a hard exclusion, which is the conservative direction: a
        record with an unreadable tag competes at a discount instead of
        being silently erased.

        On non-relational queries this is a no-op — ingested content remains
        useful for general knowledge questions.
        """
        from src.context.policies import _matches_relational_query

        if not count("ranker.authorship.relational_query",
                     bool(_matches_relational_query(user_message))):
            return items

        multipliers = {
            "first_person": 1.0,
            "mixed": 0.3,
            "unknown": 0.5,
        }

        for item in items:
            authorship = getattr(item, "authorship", None)
            if not authorship:
                metadata = getattr(item, "metadata", {}) or {}
                authorship = metadata.get("authorship") or "unknown"
            branch(
                "ranker.authorship.branch",
                authorship if authorship in multipliers else "unrecognised",
            )
            item.score = float(item.score) * multipliers.get(authorship, 0.5)

        return items

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

        if not count("ranker.state_boost.active",
                     bool(state_items) and boost != 0.0):
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

        ADR-015 amendment, implementation step 4: this boost IS the
        activation model's context-conditioning term -- the per-query half
        of ACT-R's recency+frequency-then-context structure, applied here
        rather than baked into the nightly tier because context is
        necessarily per-query while tier is one nightly value per record.
        The amendment does not add a second context-conditioning mechanism;
        this existing boost is declared to be it.

        If project_id is None (no active project), items are returned unchanged.
        """
        if not count("ranker.project.active", bool(project_id and items)):
            return items

        for item in items:
            metadata = getattr(item, "metadata", {}) or {}
            if count("ranker.project.match",
                     metadata.get("project_id") == project_id):
                item.score = float(item.score) + 0.15

        return items

    def rank(
        self,
        memory_items: list[ContextItem],
        reflection_items: list[ContextItem],
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        ranked_memory = [self._score_memory_item(item) for item in memory_items]
        ranked_reflections = [self._score_reflection_item(item) for item in reflection_items]

        # Apply multiplicative temporal decay AFTER additive scoring, BEFORE
        # final sort. This is distinct from _recency_boost (which is additive
        # and rewards freshness). Temporal decay progressively reduces old
        # records' contribution so stale content cannot win context slots on
        # semantic similarity alone. Both mechanisms coexist intentionally.
        for item in ranked_memory:
            item.score = float(item.score) * self._temporal_decay_weight(item)
        for item in ranked_reflections:
            item.score = float(item.score) * self._temporal_decay_weight(item)

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
        if count("ranker.length.under_20", len(content) < 20):
            score -= 0.10
        elif count("ranker.length.under_50", len(content) < 50):
            score -= 0.04
        elif count("ranker.length.over_1200", len(content) > 1200):
            score -= 0.03

        token_count = len(self._tokenize(content))
        if count("ranker.tokens.under_5", token_count < 5):
            score -= 0.05

        # A -0.18 low-value-prompt penalty used to sit here, triggered by an
        # exact-match list of three verbatim user utterances copied from one
        # install (CLAUDE.md Vault Privacy Rule). It is removed rather than
        # rewritten as a pattern: the class it targeted -- the user's own
        # meta-prompts retrieved back as evidence -- is handled by
        # ContextService._is_low_value_memory, which drops those records
        # outright, so a score penalty on top of a hard filter was redundant
        # for two of the three literals. The third was an ordinary question
        # about the user's own work, which is not low value; penalising it
        # was overfitting to one corpus. See src/context/low_value.py.

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

        if count("ranker.reflection.under_30_chars", len(content) < 30):
            score -= 0.08

        score += self._recency_boost(item.timestamp) * 0.5

        item.score = score
        return item

    # -----------------------------------------------------------------
    # Decay tier definitions. Each tier maps memory types to a list of
    # (max_age_days, weight) tuples. The list MUST be sorted ascending
    # by max_age_days so the first match wins. A final entry with
    # max_age_days=None serves as the fallback for anything older.
    # -----------------------------------------------------------------
    _NO_DECAY_TYPES = frozenset({"profile", "reference", "ingested"})

    _REFLECTION_DECAY = [
        (7, 1.0),
        (30, 0.80),
        (90, 0.60),
        (None, 0.40),
    ]

    _EPHEMERAL_DECAY = [
        (3, 1.0),
        (7, 0.70),
        (14, 0.45),
        (30, 0.25),
        (None, 0.10),
    ]
    _EPHEMERAL_TYPES = frozenset({"conversation", "journal", "session", "decision"})

    _DEFAULT_DECAY = [
        (3, 1.0),
        (7, 0.85),
        (14, 0.70),
        (30, 0.50),
        (90, 0.30),
        (None, 0.15),
    ]

    def _temporal_decay_weight(self, item: ContextItem) -> float:
        """Compute a multiplicative temporal decay weight for a context item.

        This is intentionally separate from _recency_boost():
        - _recency_boost is ADDITIVE and rewards freshness (a bonus).
        - _temporal_decay_weight is MULTIPLICATIVE and penalizes staleness
          (a scaling factor that shrinks old scores toward zero).

        Both coexist. The additive boost ensures recent items get a lift;
        the multiplicative decay ensures old items cannot win context slots
        on high semantic similarity alone.

        Returns 1.0 (no decay) for reference-class types (profile, reference,
        ingested) and for items whose timestamp cannot be parsed.
        """
        mem_type = getattr(item, "memory_type", "") or ""

        if count("ranker.decay.no_decay_type", mem_type in self._NO_DECAY_TYPES):
            return 1.0

        age_days = self._parse_age_days(item.timestamp)
        if count("ranker.decay.unparsed_timestamp", age_days is None):
            return 1.0

        if mem_type == "reflection":
            family, tiers = "reflection", self._REFLECTION_DECAY
        elif mem_type in self._EPHEMERAL_TYPES:
            family, tiers = "ephemeral", self._EPHEMERAL_DECAY
        else:
            family, tiers = "default", self._DEFAULT_DECAY
        branch("ranker.decay.family", family)

        for max_age, weight in tiers:
            if max_age is None or age_days <= max_age:
                branch(
                    f"ranker.decay.bucket.{family}",
                    "older" if max_age is None else f"d{max_age}",
                )
                return weight

        # Should never reach here, but safety fallback.
        reached("ranker.decay.ladder_fell_through")
        return 1.0

    def _parse_age_days(self, timestamp: str | None) -> int | None:
        """Parse a timestamp string and return age in days, or None on failure.

        Handles three formats:
        1. Unix epoch float (e.g. "1711929600.0")
        2. ISO 8601 (e.g. "2026-03-17T20:15:00+00:00")
        3. Hyphenated vault format (e.g. "2026-03-17T20-15-00")
        """
        if not timestamp:
            return None

        item_dt = None

        # Try epoch float first.
        try:
            ts = float(timestamp)
            item_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (TypeError, ValueError):
            pass

        # Try ISO format.
        if item_dt is None:
            try:
                normalized = timestamp.replace("Z", "+00:00")
                item_dt = datetime.fromisoformat(normalized)
                if item_dt.tzinfo is None:
                    item_dt = item_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        # Try hyphenated vault format: "YYYY-MM-DDTHH-MM-SS" or
        # "YYYY-MM-DDTHH-MM-SS-ffffff".
        if item_dt is None:
            try:
                # Replace hyphens after the T with colons for time part.
                if "T" in timestamp:
                    date_part, time_part = timestamp.split("T", 1)
                    segments = time_part.split("-")
                    if len(segments) >= 3:
                        colon_time = f"{segments[0]}:{segments[1]}:{segments[2]}"
                        if len(segments) == 4:
                            colon_time += f".{segments[3]}"
                        iso_str = f"{date_part}T{colon_time}"
                        item_dt = datetime.fromisoformat(iso_str)
                        if item_dt.tzinfo is None:
                            item_dt = item_dt.replace(tzinfo=timezone.utc)
            except (ValueError, IndexError):
                pass

        if item_dt is None:
            return None

        now = datetime.now(timezone.utc)
        return max((now - item_dt).days, 0)

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

        Delegates timestamp parsing to _parse_age_days so the three formats
        (Unix epoch, ISO 8601, hyphenated vault ``YYYY-MM-DDTHH-MM-SS``) are
        handled uniformly. The previous inline parser missed the hyphenated
        state-layer format — fresh state records silently scored 0.0 instead
        of +0.18, so older records could outrank them.
        """
        age_days = self._parse_age_days(timestamp)
        if age_days is None:
            branch("ranker.recency.bucket", "unparsed")
            return 0.0

        if age_days <= 7:
            branch("ranker.recency.bucket", "d7")
            return 0.18
        if age_days <= 30:
            branch("ranker.recency.bucket", "d30")
            return 0.12
        if age_days <= 90:
            branch("ranker.recency.bucket", "d90")
            return 0.06
        if age_days <= 365:
            branch("ranker.recency.bucket", "d365")
            return 0.02
        branch("ranker.recency.bucket", "older")
        return -0.03

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
