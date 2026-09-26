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

from src.context import prior
from src.context.models import ContextItem
from src.observability.guard_counters import branch, count, reached
from src.state.models import StateItem

# ADR-015 tier weights, re-derived under ADR-044's bound.
#
# These were 0.3 / 0.7 / 1.0. ADR-015 argued 0.3 as "a clear third band
# below warm, while still leaving enough headroom that a highly relevant
# cold record can outrank a weakly relevant hot one". Measured against the
# production corpus, that headroom does not exist and never did: within a
# query the rank-8/rank-1 raw cosine ratio has a median of 0.893 and a
# minimum of 0.658, so a 0.30 multiplier permitted the property in 0 of 36
# queries. Absorbing the temporal decay (ADR-044 decision 3) improves the
# composed floor from 0.03 to 0.30 and leaves it at 0 of 36.
#
# Under the bound, tier takes half the budget: sqrt(0.87216) = 0.9339.
# Warm sits at the geometric midpoint between cold and hot. Reachability
# then holds in 16 of 36 queries -- the ones whose internal spread exceeds
# the 14.7% cosine advantage a cold record now needs. That is the property
# ADR-015 claimed, delivered for the first time, on the queries where the
# embedder resolves enough difference for it to mean anything.
#
# The cost is that tier is no longer a ranking force. It is a tiebreaker.
# ADR-015's 2026-09-26 amendment records that trade and accepts it.
COLD_MULTIPLIER = prior.TIER_MIN
WARM_MULTIPLIER = prior.TIER_MIN ** 0.5


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

            # The additive recency term that stood here is gone. Recency is
            # in the prior once (ADR-044), and policy.recency_bias scaling a
            # second additive copy of it was the third of the three counts.

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
                score *= WARM_MULTIPLIER
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

        Multipliers — applied only when _matches_relational_query is True:
          first_person: 1.0  (user-authored — conversation/journal/profile)
          mixed:        0.3  (content of uncertain authorship)
          third_party:  0.0  (books, articles, other voices — filtered out)
          unknown:      0.5  (conservative middle pending re-tag)

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
            "third_party": 0.0,
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
        """Apply the metadata prior once, then order.

        ADR-044: this used to be an additive pile here plus a second one in
        semantic_search, followed by a multiplicative temporal decay. The
        pile is now a single bounded multiplier (src/context/prior.py) and
        the decay is absorbed into TieringService's per-type halflife, so
        age reaches ranking once, through tier, rather than three times.
        """
        ranked_memory = [self._score_memory_item(item) for item in memory_items]
        ranked_reflections = [self._score_reflection_item(item) for item in reflection_items]

        ranked_memory.sort(key=lambda item: item.score, reverse=True)
        ranked_reflections.sort(key=lambda item: item.score, reverse=True)

        return ranked_memory, ranked_reflections

    def _score_memory_item(self, item: ContextItem) -> ContextItem:
        """score = similarity x prior. Tier is applied in apply_policy.

        ADR-044 decision 2. Every additive term that used to live here --
        type, role, content_kind, length, token count, recency -- is now
        either inside the bounded prior or gone:

          type        removed. rank.type.conversation / .reflection /
                      .other are all in Sobol's no_solo_delivery_effect
                      list and Morris's no-effect class on delivery, and
                      they were the terms counted twice.
          role        moved out of the budget to a hard predicate
                      (ADR-044 4a, src/context/role_predicate.py).
          content_kind, length, recency
                      retained inside the prior, magnitudes re-derived
                      from Sobol ST on delivery.
          tokens<5    removed. Subsumed by the length term it duplicates
                      and never separately measured.
        """
        content = item.content.lower().strip()
        metadata = getattr(item, "metadata", {}) or {}

        item.score = float(item.score) * prior.assemble(
            content_kind=metadata.get("content_kind"),
            content_length=len(content),
            recency_bucket=self._recency_bucket(item.timestamp),
        )
        return item

    def _score_reflection_item(self, item: ContextItem) -> ContextItem:
        """Reflections take the same prior, flagged as derived.

        The 0.95 base discount and the -0.08 short-reflection penalty are
        gone: refl.base_discount is in Sobol's no_solo_delivery_effect
        list, and ranker.reflection.under_30_chars fired 0 times in the
        personal-vault window. Neither has a defensible magnitude, so both
        take the smallest value consistent with the contract.
        """
        content = item.content.lower().strip()
        metadata = getattr(item, "metadata", {}) or {}

        item.score = float(item.score) * prior.assemble(
            content_kind=metadata.get("content_kind"),
            content_length=len(content),
            recency_bucket=self._recency_bucket(item.timestamp),
            is_reflection=True,
        )
        return item

    # The decay ladders and _temporal_decay_weight that stood here are
    # absorbed into TieringService as a per-type halflife (ADR-044 decision
    # 3). They were a second multiplicative age model compounding with
    # tier's, governed by no ADR, and the larger of the two in production:
    # ranker.decay.bucket.ephemeral=older fired 256 of 256 times, a flat
    # x0.10 on nearly everything that reached it. Age now reaches ranking
    # once, through tier.
    _NO_DECAY_TYPES = frozenset({"profile", "reference", "ingested"})

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

    def _recency_bucket(self, timestamp: str | None) -> str:
        """Which recency band this record falls in.

        The bucket names are the interface; the magnitudes moved into
        src/context/prior.py, where they are derived from Sobol ST rather
        than from the eval that does not exist. Previously this returned
        an additive bonus and was called at three separate sites -- once
        in apply_policy scaled by recency_bias, once per memory item and
        once at half weight per reflection -- which is why #207 counted
        recency three times over on the additive side alone.
        """
        age_days = self._parse_age_days(timestamp)
        if age_days is None:
            return branch("ranker.recency.bucket", "unparsed")
        if age_days <= 7:
            return branch("ranker.recency.bucket", "d7")
        if age_days <= 30:
            return branch("ranker.recency.bucket", "d30")
        if age_days <= 90:
            return branch("ranker.recency.bucket", "d90")
        if age_days <= 365:
            return branch("ranker.recency.bucket", "d365")
        return branch("ranker.recency.bucket", "older")

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
