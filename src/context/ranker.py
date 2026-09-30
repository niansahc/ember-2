"""
src/context/ranker.py

ContextRanker applies policy-based scoring adjustments to retrieved items
and produces the final ranked ordering.

See the class-level docstring on ContextRanker for what remains here and
where each part of it gets its authority. This docstring used to describe a
"clear priority hierarchy" of type and role boosts, and to claim all the
constants were empirical -- a claim the class docstring immediately below
now exists to refute. Two docstrings, one contradicting the other, is worse
than either alone.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.context import prior
from src.context.models import ContextItem
from src.observability.guard_counters import branch, count
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

    This docstring used to claim every constant here was "tuned empirically
    against the retrieval eval (tools/eval_retrieval.py, 15 benchmark cases)".
    That provenance does not exist: the eval has 5 cases and no graded
    relevance, so it could not have produced the numbers attributed to it.
    ADR-044 records the finding, and it is the reason the prior's magnitudes
    were re-derived rather than carried forward.

    What remains here, and where its authority comes from:

      the tier multipliers    ADR-015, re-derived under ADR-044's bound. See
                              COLD_MULTIPLIER above.
      the policy preference
      terms (+0.20, +0.22,
      -0.05, +0.03)           still undefended magnitudes. They are
                              query-CONDITIONAL rather than query-independent,
                              so the ADR-044 bound does not cover them, and
                              they were not re-derived. Treat any number in
                              apply_policy as provisional.
      the authorship
      multipliers             UAT-005. A gate, not a class constant.
      the project boost       ADR-007, declared by ADR-015's amendment to be
                              the activation model's context term.

    The metadata prior is not here at all. It lives in src/context/prior.py,
    where its magnitudes are derived from Sobol ST on the delivery endpoint
    and its authority over similarity is capped by a tested bound.
    """

    def apply_policy(
        self,
        items: list[ContextItem],
        policy,
        *,
        channel_weight: float | None = None,
    ) -> list[ContextItem]:
        """Apply the channel weight and the tier multiplier. Nothing additive.

        `channel_weight` is the weight for the LIST being ranked, applied
        uniformly to every item in it. It used to be chosen per item from
        `item.item_type == "reflection"`, and that was a defect: `reflection`
        is in SQLITE_MEMORY_TYPES, so reflection records reach memory_items,
        where they took reflection_weight (1.4 on the reflective policy) while
        their neighbours took memory_weight (0.7). A 2x swing between two
        records in the SAME delivered list, from a term that is supposed to be
        per-channel tuning.

        That is why the weight sits outside the ADR-044 bound and can stay
        there: a uniform positive scale on a list cannot reorder that list.
        build_context already calls this separately per channel, so the caller
        knows which weight applies and passes it. The per-item fallback is kept
        only for callers that have not been updated, and it preserves the old
        behaviour rather than silently changing it.

        The three additive preference terms that stood here (+0.20 experience,
        +0.22 active work, -0.05/+0.03 exact) are gone. They are now arms of
        the prior's policy family, bounded with everything else -- see
        src/context/prior.py. The largest was 2.7x the entire measured cosine
        spread, so ADR-044's claim that composition was bounded was false while
        they were here.
        """
        adjusted: list[ContextItem] = []

        for item in items:
            score = float(item.score)

            if channel_weight is not None:
                score *= channel_weight
            elif item.item_type == "reflection":
                score *= policy.reflection_weight
            else:
                score *= policy.memory_weight

            # The additive recency term that stood here is gone too. Recency is
            # in the prior once (ADR-044), and policy.recency_bias scaling a
            # second additive copy of it was the third of the three counts.

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
                # The branch record IS the body: profile takes no multiplier.
                branch("ranker.tier", "profile_bypass")
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

        ADR-044 (2026-09-30): this no longer adds anything. The +0.15 was the
        last additive term in the composition and it landed AFTER the tier
        multiply, so tier could not attenuate it -- the ordering defect
        decision 1 names, surviving as one term after the rest of the pile was
        consolidated. It is now an arm of the prior's project family, bounded
        with everything else, and applied in rank() with the other factors.

        What remains here is the MARKER: it records whether the record matches
        the active project, so rank() can read it without being handed the
        project id separately. Kept as a method rather than folded into rank()
        because build_context calls it per channel and the guard counters that
        record project activation are the harness's only view of ADR-007.

        If project_id is None (no active project), nothing matches.
        """
        if not count("ranker.project.active", bool(project_id and items)):
            for item in items:
                item.project_match = False
            return items

        for item in items:
            metadata = getattr(item, "metadata", {}) or {}
            item.project_match = count(
                "ranker.project.match", metadata.get("project_id") == project_id
            )

        return items

    def rank(
        self,
        memory_items: list[ContextItem],
        reflection_items: list[ContextItem],
        policy=None,
    ) -> tuple[list[ContextItem], list[ContextItem]]:
        """Apply the metadata prior once, then order.

        ADR-044: this used to be an additive pile here plus a second one in
        semantic_search, followed by a multiplicative temporal decay. The
        pile is now a single bounded multiplier (src/context/prior.py) and
        the decay is absorbed into TieringService's per-type halflife, so
        age reaches ranking once, through tier, rather than three times.

        `policy` arrived with the 2026-09-30 amendment, when the per-policy
        preference terms moved into the prior. They are conditional on the
        policy, so the one place that applies the prior has to know it. It is
        optional because the prior's policy family is the identity without one,
        which is the correct answer for a caller that has no policy rather than
        a reason to refuse.
        """
        ranked_memory = [self._score_memory_item(item, policy) for item in memory_items]
        ranked_reflections = [
            self._score_reflection_item(item, policy) for item in reflection_items
        ]

        ranked_memory.sort(key=lambda item: item.score, reverse=True)
        ranked_reflections.sort(key=lambda item: item.score, reverse=True)

        return ranked_memory, ranked_reflections

    def _score_memory_item(self, item: ContextItem, policy=None) -> ContextItem:
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

        The 2026-09-30 amendment added two more families to the same prior --
        the per-policy preference terms and ADR-007's project match -- so the
        additive stages above and below this one are now empty of anything a
        bound would have to cover.
        """
        return self._apply_prior(item, is_reflection=False, policy=policy)

    def _score_reflection_item(self, item: ContextItem, policy=None) -> ContextItem:
        """Reflections take the same prior, flagged as derived.

        The 0.95 base discount and the -0.08 short-reflection penalty are
        gone: refl.base_discount is in Sobol's no_solo_delivery_effect
        list, and ranker.reflection.under_30_chars fired 0 times in the
        personal-vault window. Neither has a defensible magnitude, so both
        take the smallest value consistent with the contract.

        Delegates rather than duplicating. The two paths were byte-identical
        apart from one keyword, and during this refactor the trace harness's
        copy of the reflection path silently lost two factors for exactly that
        reason -- two near-identical bodies are two places to keep in step.
        """
        return self._apply_prior(item, is_reflection=True, policy=policy)

    def _policy_arm(self, item: ContextItem, policy) -> str:
        """Which policy-preference arm this record takes, or "none".

        The predicates are the same ones apply_policy used when these were
        additive terms; only where their answer is spent has changed. Kept here
        rather than in prior.py because they read ContextItem and prior.py
        deliberately knows nothing about it.
        """
        if policy is None:
            return "none"

        content = item.content.lower()
        metadata = getattr(item, "metadata", {}) or {}
        content_kind = metadata.get("content_kind")

        prefer_experiences = bool(getattr(policy, "prefer_experiences", False))
        prefer_active_work = bool(getattr(policy, "prefer_active_work", False))
        prefer_exact = bool(getattr(policy, "prefer_exact_matches", False))

        # The counters that recorded these as additive terms are kept, with the
        # same site names, so the traffic window's history stays continuous
        # across the change of mechanism.
        count("ranker.policy.prefer_experiences_enabled", prefer_experiences)
        count("ranker.policy.prefer_active_work_enabled", prefer_active_work)
        count("ranker.policy.prefer_exact_matches_enabled", prefer_exact)

        experience_fired = prefer_experiences and count(
            "ranker.policy.prefer_experiences_fired",
            content_kind == "experience" or self._looks_like_experience(content),
        )
        active_work_fired = prefer_active_work and count(
            "ranker.policy.prefer_active_work_fired",
            self._looks_like_active_work(content, metadata),
        )
        if prefer_exact:
            count("ranker.policy.exact_match_question", content_kind == "question")

        return prior.policy_branch(
            prefer_experiences=prefer_experiences,
            prefer_active_work=prefer_active_work,
            prefer_exact_matches=prefer_exact,
            experience_fired=bool(experience_fired),
            active_work_fired=bool(active_work_fired),
            is_question=content_kind == "question",
        )

    def _apply_prior(
        self, item: ContextItem, *, is_reflection: bool, policy=None
    ) -> ContextItem:
        """score = score x prior. The one place the prior is applied.

        Every bounded factor composes here, in one multiply, which is what
        makes the ordering defect ADR-044 decision 1 names structurally
        impossible: multiplication commutes, so there is no longer a stage
        order for a term to land on the wrong side of.
        """
        metadata = getattr(item, "metadata", {}) or {}

        # strip() without lower(): only the LENGTH is read. The lowered copy
        # fed the content-prefix term and the tokenizer, both of which ADR-044
        # deleted, so lowering allocated a full second copy of every record
        # body to measure it.
        item.score = float(item.score) * prior.assemble(
            content_kind=metadata.get("content_kind"),
            content_length=len(item.content.strip()),
            recency_bucket=self._recency_bucket(item.timestamp),
            is_reflection=is_reflection,
            policy_arm=self._policy_arm(item, policy),
            project_match=bool(getattr(item, "project_match", False)),
        )
        return item

    # The decay ladders and _temporal_decay_weight that stood here are
    # absorbed into TieringService as a per-type halflife (ADR-044 decision
    # 3). They were a second multiplicative age model compounding with
    # tier's, governed by no ADR, and the larger of the two in production:
    # ranker.decay.bucket.ephemeral=older fired 256 of 256 times, a flat
    # x0.10 on nearly everything that reached it. Age now reaches ranking
    # once, through tier.
    #
    # _NO_DECAY_TYPES went with them. The reference-class exemption it encoded
    # is preserved, in TieringService, which is the module that now owns the
    # age curve -- keeping a copy here would have left two sources of truth
    # for which types decay, which is the defect ADR-044 decision 3 exists to
    # remove.

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
        return self._bucket_for_age(self._parse_age_days(timestamp))

    @staticmethod
    def _bucket_for_age(age_days: int | None) -> str:
        """The ladder itself, on an age a caller has already parsed.

        Split out so a caller holding `age_days` does not have to hand back a
        timestamp string to have it re-parsed. The trace harness holds exactly
        that, and was paying a third parse of the same timestamp per candidate
        to get a bucket name.

        No guard counter here. `prior.recency` records the same decision on the
        same value one call later, and two counter rows for one branch means
        the traffic-window inventory accounts the same fact twice -- which it
        was doing, with `ranker.recency.bucket=d7` and `prior.recency=d7` both
        declared as if they were independent observations.
        """
        if age_days is None:
            return "unparsed"
        if age_days <= 7:
            return "d7"
        if age_days <= 30:
            return "d30"
        if age_days <= 90:
            return "d90"
        if age_days <= 365:
            return "d365"
        return "older"

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

    # _tokenize went with the token-count penalty it existed for (ADR-044: the
    # <5-token term was subsumed by the length term it duplicated and was
    # never separately measured). ContextRetriever and ContextService keep
    # their own tokenizers for Jaccard overlap, which is a different job.
