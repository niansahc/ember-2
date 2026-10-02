"""
src/context/service.py

ContextService orchestrates context assembly for each request. It is
the central coordinator between retrieval, ranking, type gating, and
prompt formatting — the cognitive layer's main entry point.

Pipeline: classify query intent → retrieve candidates → relevance gate →
type gate (ADR-018) → policy weighting → project boost → rank → echo/meta
filter → dedup → diversity selection → format into ContextPacket.
"""

import logging
import re
from dataclasses import dataclass

from src.context.formatter import ContextFormatter
from src.core.config import get_ember_debug
from src.observability import guard_counters
from src.observability.guard_counters import branch, count

logger = logging.getLogger("ember.context_service")


def _log_context_selection(selected_memory) -> None:
    """Log the final selected memory items. Gated by EMBER_DEBUG.

    Each item's content is vault material. Defense in depth: even when a
    caller constructs ContextService(debug=True), nothing reaches stdout
    or the warning channel unless EMBER_DEBUG is also explicitly set.
    Matches the PR #73 privacy-gating precedent.
    """
    if not get_ember_debug():
        return
    for item in selected_memory:
        logger.debug("[CTX] %s: %s", item.item_type, item.content[:120])


# ---------------------------------------------------------------------------
# AI documentation quarantine — prevents identity contamination from web
# search results about other AI systems (Claude, GPT, Gemini, etc.).
# ---------------------------------------------------------------------------

AI_SYSTEM_NAMES: frozenset[str] = frozenset({
    "claude", "anthropic", "gpt", "chatgpt", "openai",
    "gemini", "google deepmind", "llama", "meta ai",
    "mistral", "copilot", "perplexity", "qwen", "ollama",
})

AI_DOC_MARKERS: tuple[str, ...] = (
    "training cutoff", "context window", "parameters",
    "model card", "system prompt", "api documentation",
    "tokens per", "knowledge cutoff", "token limit",
    "parameter count", "training data",
)

# Escape hatch patterns — if the user is explicitly asking about another
# AI system (not Ember), quarantined content should be surfaced.
_AI_INQUIRY_PATTERNS = (
    "tell me about claude", "tell me about gpt", "tell me about gemini",
    "compare", "how does claude", "how does gpt", "what is claude",
    "what is chatgpt", "what is openai", "what is anthropic",
)


def _quarantine_ai_docs(
    web_items: list[dict],
    user_message: str,
) -> tuple[list[dict], list[dict]]:
    """Split web results into (safe, quarantined).

    Quarantines (does not discard) web results that appear to be AI
    system documentation or model cards. These describe other systems
    (Claude, GPT, etc.) and could cause identity contamination if
    injected into Ember's context.

    Escape hatch: if the user is explicitly asking about another AI
    system, all results pass through unfiltered.
    """
    user_lower = user_message.lower()
    if count("web_quarantine.ai_inquiry_escape_hatch",
             any(pattern in user_lower for pattern in _AI_INQUIRY_PATTERNS)):
        return web_items, []

    safe: list[dict] = []
    quarantined: list[dict] = []

    for item in web_items:
        combined = (
            (item.get("title", "") + " " + item.get("snippet", ""))
            .lower()
        )
        ai_name_count = sum(1 for name in AI_SYSTEM_NAMES if name in combined)
        has_doc_marker = any(marker in combined for marker in AI_DOC_MARKERS)

        if count("web_quarantine.ai_doc_detected",
                 ai_name_count >= 2 or has_doc_marker):
            quarantined.append(item)
        else:
            safe.append(item)

    return safe, quarantined
from src.context.low_value import is_low_value_content
from src.context.models import ContextPacket
from src.context.policies import classify_query
from src.context import role_predicate
from src.context.ranker import ContextRanker
from src.context.retriever import ContextRetriever
from src.context import prior as _prior
from src.tools.web_search import web_search


@dataclass(frozen=True)
class DiversityWeights:
    """The magnitudes `_diversity_score` penalises with.

    Declared as an object so the trace harness can perturb them. They were
    inline literals, which meant the sensitivity vector could not reach them:
    `tools/retrieval_trace/compose.py` had no diversity stage, so a converged
    Sobol run measured a delivery endpoint whose selection function was a
    constant. Copying the numbers into params.py instead would have made a
    second definition of a magnitude that is supposed to have one derivation.

    One of the four is derived and three are the shipped values carried forward
    unchanged, because three of the four cannot be measured on the current corpus
    at all -- see ADR-044's amendment and its reachability section.

    `similarity_share` multiplies a MAX over the selected set and is therefore
    capped at its own value. The other three are SUMMED per already-selected
    neighbour and grow without limit in len(selected). That asymmetry is what
    SELECTION_BAND has to resolve, not the individual sizes.
    """

    # The similarity term's share of the band, at max_jaccard = 1. Derived: it
    # is the one selection term with a resolved ST (0.1180 +/- 0.0078 on the
    # delivery endpoint, 8th of 27 swept parameters), so it is entitled to the
    # whole band at full overlap -- two identical records cost each other exactly
    # the authority a tie-breaker may have, and partial overlap costs
    # proportionally less. Replaces an absolute 0.70, which on a composed score
    # of 0.4 to 0.8 reached 175% of the record.
    similarity_share: float = 1.0

    # The three per-neighbour sums, carried at their shipped values. NOT derived:
    # all three are solo-flat on the delivery endpoint and cannot be measured on
    # this corpus, so these numbers are the ones that were already here rather
    # than numbers anybody computed. The total cap in _diversity_score is what
    # bounds them; see ADR-044's amendment on why capping beats guessing.
    same_type: float = 0.05
    same_doc: float = 0.22
    same_title: float = 0.08


DIVERSITY_WEIGHTS = DiversityWeights()

# How far the selection objective may move a record, as a fraction of that
# record's own composed score.
#
# ADR-044 / #255. The three per-neighbour terms are SUMMED, so they grew without
# limit in len(selected): at a limit of 6 they reached 0.25, 1.10 and 0.40, a
# total of 1.75 against a composed score of roughly 0.4 to 0.8. A selection
# objective able to subtract more than twice a record's whole score is not
# ordering records, it is overriding relevance.
#
# The cap is derived, not chosen. A tie-breaker's legitimate authority is
# tie-breaking AMONG RECORDS OF COMPARABLE RELEVANCE, and the measured width of
# "comparable" is the embedder's own top-k spread: RELATIVE_SPREAD, the same
# 0.0815 / 0.6375 the prior's bound comes from (#236). Two records within that
# band are comparable and diversity may reorder them; two records further apart
# are not, and diversity may not overturn the similarity signal. Expressed as a
# fraction of the record's own score rather than an absolute, because the penalty
# applies to a composed score whose scale is not fixed.
#
# This is a STRUCTURAL bound, and deliberately so. The individual magnitudes of
# the three sums are not measurable on this corpus -- all three are solo-flat on
# the delivery endpoint, and same_doc's triggering records turn out to be the ones
# role exclusion already removes -- so a derived magnitude for any of them would be
# a number invented for a term the corpus cannot exercise. Capping the total makes
# the individual sizes moot rather than guessed: whatever they are, together they
# cannot exceed the band.
SELECTION_BAND = _prior.RELATIVE_SPREAD


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
        skip_web_search: bool = False,
        read_only: bool = False,
    ) -> ContextPacket:
        """Assemble the context packet for one turn.

        read_only suppresses the retrieval-stats write at the end of
        assembly, and nothing else -- the packet returned is identical
        either way. It exists because delivery is the only input to a
        record's heat under ADR-015's activation model, so any caller that
        builds a packet in order to LOOK at retrieval rather than to answer
        a turn is, by default, changing the thing it is measuring. An
        investigative read that promotes 6 records to hot is not a
        measurement; it is an intervention with a report attached.

        A per-call flag only protects callers that remember it. For replay
        and ablation, arm the process too:
        src.retrieval.retrieval_stats.retrieval_stats_disabled() or
        EMBER_RETRIEVAL_STATS_READ_ONLY=1, which gate the write inside the
        store rather than here.

        Callers that answer a real user turn must leave this False. The
        activation model depends on genuine deliveries being recorded, and
        a chat path that silently stopped writing would look identical to
        this fix while quietly disabling tiering's only upward path.
        """
        policy = classify_query(user_message)
        # Guard hit counters record this turn only if it is a real one:
        # not read_only, and not inside a suppressed-stats scope. Every
        # investigative caller sets one or the other, so eval harnesses,
        # the ablation and the trace capture are excluded structurally
        # rather than by remembering to turn something off. The scope also
        # refuses to open under pytest -- test fixtures exercise guards
        # production never does, and counting them would report a rule as
        # live when no real record has ever matched it.
        from src.retrieval.retrieval_stats import retrieval_stats_disabled_now

        # A declared measurement window overrides both exclusions. That is
        # the only way to count guards against a corpus that must not be
        # written to, which is the whole of a read-only window over real
        # memory. Nothing sets it by default.
        with guard_counters.recording(
            enabled=guard_counters.window_override_active()
            or (not read_only and not retrieval_stats_disabled_now())
        ):
            return self._build_context(user_message, image_data, project_id,
                                       skip_web_search, read_only, policy)

    def _build_context(
        self,
        user_message: str,
        image_data: list[str] | None,
        project_id: str | None,
        skip_web_search: bool,
        read_only: bool,
        policy,
    ) -> ContextPacket:
        """Assembly proper. Split out so the counter scope wraps all of it."""

        web_items: list[dict] = []
        # skip_web_search=True when ask-first mode is active — the search
        # should not execute until the user confirms. Without this gate,
        # the context service fetches results during assembly and the SSE
        # stream shows sources alongside the "want me to search?" question.
        if policy.use_web_search and not skip_web_search:
            raw_web = web_search(user_message)
            web_items, quarantined = _quarantine_ai_docs(raw_web, user_message)
            if quarantined:
                logger.info(
                    "[CONTEXT] Quarantined %d AI-doc web result(s)", len(quarantined)
                )

        state_items, task_items, memory_items, reflection_items, query_embedding = self.retriever.retrieve(user_message)
        state_items = self.ranker.apply_state_boost(state_items, policy)

        # Relevance gate for default policy: if no non-profile items have
        # raw cosine similarity >= threshold, suppress vault memory entirely.
        # Prevents general knowledge queries from getting vault-based coaching.
        # Profile items are exempt — identity queries should always surface.
        if count("relevance_gate.evaluated", policy.name == "default"):
            from src.core.config import get_retrieval_min_raw_score
            min_raw = get_retrieval_min_raw_score()
            # Lower threshold for ingested items. ChatGPT exports
            # have weaker embedding matches (longer chunks, mixed-role text)
            # but are still useful context. Use 0.15 for ingested vs the
            # standard threshold for other types.
            _INGESTED_MIN_RAW = 0.15
            non_profile = [i for i in memory_items if getattr(i, "memory_type", "") != "profile"]
            non_profile_non_ingested = [
                i for i in non_profile if getattr(i, "memory_type", "") != "ingested"
            ]
            ingested_only = [
                i for i in non_profile if getattr(i, "memory_type", "") == "ingested"
            ]
            max_raw_standard = max(
                (getattr(i, "metadata", {}).get("raw_score", 0.0) for i in non_profile_non_ingested),
                default=0.0,
            )
            max_raw_ingested = max(
                (getattr(i, "metadata", {}).get("raw_score", 0.0) for i in ingested_only),
                default=0.0,
            )
            # Gate passes if EITHER standard types clear their threshold OR
            # ingested clears its lower threshold.
            if count(
                "relevance_gate.suppressed_non_profile",
                max_raw_standard < min_raw and max_raw_ingested < _INGESTED_MIN_RAW,
            ):
                memory_items = [i for i in memory_items if getattr(i, "memory_type", "") == "profile"]
                reflection_items = []

        # ADR-018: Apply intent-aware type gating before ranking.
        # Profile items bypass type gating — identity context is never suppressed.
        memory_items = self._apply_type_gate(memory_items, policy)
        reflection_items = self._apply_type_gate(reflection_items, policy)

        # Per-channel weights, passed explicitly. Chosen per ITEM until
        # ADR-044's 2026-09-30 amendment, which made a reflection record
        # arriving through the memory channel take reflection_weight while its
        # neighbours in the same list took memory_weight -- a 2x reordering
        # inside one delivered list from a term meant to tune a channel. A
        # uniform scale per list cannot reorder that list, which is what lets
        # the weight stay outside the composed bound.
        memory_items = self.ranker.apply_policy(
            memory_items, policy, channel_weight=policy.memory_weight
        )
        reflection_items = self.ranker.apply_policy(
            reflection_items, policy, channel_weight=policy.reflection_weight
        )

        # Authorship multiplier on relational queries.
        # No-op on non-relational queries. When the query is about the user's
        # personal relationships or identity domains ("my son", "my partner",
        # "my health"), third-party ingested content is zeroed out so kinship
        # answers don't synthesize from books or the user's old ChatGPT
        # dialogue about other people.
        # ADR-044 4a: role is a predicate, not a score term. Applied before
        # the authorship multiplier so an assistant turn is gone rather than
        # discounted. Unconditional, not scoped to relational queries -- see
        # role_predicate's "why this is not scoped by query".
        memory_items = role_predicate.apply(memory_items)
        memory_items = self.ranker.apply_authorship_scoring(memory_items, user_message)

        # Boost memories from the active project (ADR-007)
        memory_items = self.ranker.apply_project_boost(memory_items, project_id)
        reflection_items = self.ranker.apply_project_boost(reflection_items, project_id)

        ranked_memory, ranked_reflections = self.ranker.rank(
            memory_items, reflection_items, policy
        )

        normalized_user_message = self._normalize_text(user_message)

        # Filter echo/meta/low-value content directly from ranked results.
        # _relevance_hits was removed — it dropped semantically correct results
        # that used synonyms or related terms (e.g. "work" for query "working").
        # The vector search + ranker already handle relevance ranking.
        filtered_memory = [
            item
            for item in ranked_memory
            if not count("filter.echo_or_meta",
                         self._is_echo_or_meta_memory(item, normalized_user_message))
            and not count("filter.low_value",
                          self._is_low_value_memory(item))
        ]

        if count("filter.took_everything_fallback",
                 bool(ranked_memory) and not filtered_memory):
            filtered_memory = ranked_memory

        deduped_memory = self._deduplicate(filtered_memory)
        deduped_reflections = self._deduplicate(ranked_reflections)

        memory_limit = self._memory_limit_for_policy(policy.name)
        reflection_limit = self._reflection_limit_for_policy(policy.name)

        # Profile items are guaranteed slots — partition them out first so the
        # ranker's score-based ordering cannot push them below the limit cutoff.
        #
        # memory_limit applies to the non-profile channel only; profile is not
        # charged against it. Profile already has its own prompt section, and
        # The prompt partitions it out a second time and caps non-profile at
        # render_window.MEMORY_RENDER_SLOTS independently -- so subtracting the
        # profile count
        # here reserved no prompt space for profile. It only starved the other
        # channel before it reached a render layer that was going to separate
        # them anyway.
        #
        # This also makes profile consistent with every other always-on layer.
        # Nature, the lodestone seed and living values, state, tasks and
        # reflections each have their own section and their own budget; profile
        # was the only one billed to the retrieval window. On the reflective
        # policy, whose limit is 4, the subtraction left one non-profile slot
        # and killed _select_diverse_memory's round-robin outright.
        #
        # The guarantee is unchanged: three profile records, no gate, no
        # threshold, no ordering. See the council review on #211 follow-up.
        profile_items = [i for i in deduped_memory if i.memory_type == "profile"]
        other_items = [i for i in deduped_memory if i.memory_type != "profile"]
        remaining_limit = memory_limit

        count("reserved_slots.profile_present", bool(profile_items))
        count("reserved_slots.profile_over_limit",
              len(profile_items) > memory_limit)
        count("reserved_slots.non_profile_truncated",
              len(other_items) > remaining_limit)

        if branch("selection.mode",
                  "diversity" if policy.diversity else "score_order") == "diversity":
            selected_other = self._select_diverse_memory(
                other_items,
                limit=remaining_limit,
            )
        else:
            selected_other = other_items[:remaining_limit]

        selected_memory = profile_items + selected_other

        selected_reflections = deduped_reflections[:reflection_limit]

        if self.debug:
            _log_context_selection(selected_memory)

        packet = self.formatter.format(
            user_message=user_message,
            memory_items=selected_memory,
            reflection_items=selected_reflections,
            state_items=state_items,
            task_items=task_items,
            web_items=web_items,
            image_data=image_data or [],
        )
        # Attach pre-computed query embedding for downstream use (lodestone
        # resolver in prompt builder). Avoids a redundant embed_text() call.
        packet.query_embedding = query_embedding

        # ADR-015 retrieval stats, issue #227. The write is armed here and
        # fired by the adapter once the prompt is final, against the records
        # the prompt actually rendered rather than every candidate in the
        # packet. build_context cannot do it itself: it returns before the
        # prompt exists, and the slice is decided in prompt_builder.
        #
        # read_only arms nothing at all, which keeps #206's guarantee
        # structural -- there is no writer to reach rather than a branch that
        # declines to call one.
        if not read_only:
            packet.arm_delivery_recorder(self._update_retrieval_stats)
        elif self.debug:
            logger.info(
                "[CONTEXT] read_only: no retrieval-stats recorder armed for "
                "%d candidate record(s)",
                len(selected_memory) + len(selected_reflections),
            )

        # Zero-hit signal. If the query was relational
        # AND every non-profile memory item zeroed out under authorship
        # scoring, flag the packet so the prompt builder renders the
        # "no personal memory on this topic — don't synthesize from ingested
        # content" authority-rules line. Profile records don't count — they
        # surface on every turn and aren't evidence of specific personal
        # grounding for this query.
        from src.context.policies import _matches_relational_query
        if count("zero_hit_signal.relational_query",
                 bool(_matches_relational_query(user_message))):
            non_profile = [
                i for i in selected_memory
                if getattr(i, "memory_type", "") != "profile"
            ]
            if count("zero_hit_signal.all_non_profile_zeroed",
                     bool(non_profile)
                     and all(float(getattr(i, "score", 0.0)) == 0.0
                             for i in non_profile)):
                packet.relational_query_empty = True
            elif count("zero_hit_signal.profile_only", not non_profile):
                # Nothing but profile items — also treat as empty for this
                # signal. Kinship/identity questions should surface the gap
                # rather than answer from onboarding boilerplate.
                packet.relational_query_empty = True

        return packet

    def _update_retrieval_stats(self, items: list) -> None:
        """
        ADR-015 amendment, implementation step 4: update frequency_score and
        last_retrieved_at for selected records, keyed by ContextItem.store_id
        (the real vectors.id) -- not `.id`, which is a different identifier
        (path/chunk_id) used for session-scoped hedge tracking and, for most
        memory types, never matched any vectors row. Keying off `.id` here
        was the reason retrieval stats never updated: this is intentionally
        not a fallback to `.id` for that same reason.

        Only called on records that made it into the final context packet.
        Runs in a try/except so retrieval stat failures never crash context building.
        """
        try:
            from src.retrieval.retrieval_stats import retrieval_stats_disabled_now

            # Process-level suppression, independent of the read_only
            # argument: a replay harness that forgot the flag is still
            # covered, and so is anything that reaches the stores by a path
            # that does not pass through build_context at all.
            if retrieval_stats_disabled_now():
                if self.debug:
                    logger.info(
                        "[CONTEXT] retrieval-stat write suppressed for %d record(s): "
                        "read-only mode is active",
                        len(items),
                    )
                return

            from src.retrieval.semantic_search import _get_memory_store, _get_sqlite_store

            # Collect record IDs by store
            memory_ids = []
            ingested_ids = []

            for item in items:
                record_id = getattr(item, "store_id", None) or ""
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
        suppress = policy.suppress_memory_types
        eligible = policy.eligible_memory_types
        min_score = policy.min_score

        kept = []
        for i in items:
            mem_type = getattr(i, "memory_type", None)
            if count("type_gate.profile_bypass", mem_type == "profile"):
                kept.append(i)
                continue
            if count("type_gate.suppressed_type",
                     bool(suppress) and mem_type in suppress):
                continue
            if count("type_gate.not_eligible_type",
                     eligible is not None and mem_type not in eligible):
                continue
            if count("type_gate.below_min_score",
                     getattr(i, "score", 0.0) < min_score):
                continue
            kept.append(i)
        return kept

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

        if count("echo_filter.empty_content", not content):
            return True

        meta_markers = (
            "user asked:",
            "ember responded:",
            "assistant responded:",
            "assistant said:",
            "question:",
            "answer:",
        )

        if count("echo_filter.meta_marker",
                 any(marker in content for marker in meta_markers)):
            return True

        if count("echo_filter.query_verbatim_in_content",
                 bool(normalized_user_message)
                 and normalized_user_message in content):
            return True

        similarity = self._jaccard_similarity(
            self._tokenize(content),
            self._tokenize(normalized_user_message),
        )

        # 0.55 Jaccard threshold: content sharing >55% of its tokens with
        # the user message is likely a near-echo (the user's own question
        # stored as a conversation turn, or a prior assistant response that
        # paraphrased the question). Tuned to catch echoes without dropping
        # semantically related but distinct content — 0.50 produced false
        # positives on legitimate related memories, 0.60 let echoes through.
        return count("echo_filter.jaccard_over_0_55", similarity > 0.55)

    def _is_low_value_memory(self, item) -> bool:
        content = self._normalize_text(item.content)
        metadata = getattr(item, "metadata", {}) or {}

        if count("low_value_filter.under_40_chars", len(content) < 40):
            return True

        # Previously an exact-match list plus a marker list, both built from
        # verbatim user utterances copied out of one install's conversation
        # history -- a Vault Privacy Rule violation, and dead weight on every
        # other install since an exact-match list cannot fire on sentences
        # nobody there has written. Replaced by patterns over the same three
        # classes: model boilerplate, response-style feedback, and the user's
        # own meta-prompts to the assistant. See src/context/low_value.py.
        if count("low_value_filter.pattern_match", is_low_value_content(content)):
            return True

        if count("low_value_filter.short_question",
                 metadata.get("content_kind") == "question" and len(content) < 120):
            return True

        return False

    def _deduplicate(self, items: list) -> list:
        seen = set()
        deduped = []

        for item in items:
            key = self._normalize_text(item.content)
            if not count("selection.dedup.duplicate_content", key in seen):
                deduped.append(item)
                seen.add(key)

        return deduped

    def _select_diverse_memory(
        self, items: list, limit: int, weights: DiversityWeights = DIVERSITY_WEIGHTS
    ) -> list:
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
                    grouped_items[group_name], selected, weights
                )

                if count(f"diversity.group_yielded.{group_name}", bool(candidate)):
                    selected.append(candidate)
                    grouped_items[group_name].remove(candidate)
                    made_progress = True

                    if count("diversity.limit_reached_mid_round",
                             len(selected) >= limit):
                        break

            if count("diversity.round_made_no_progress", not made_progress):
                break

        if count("diversity.fell_short_of_limit", len(selected) < limit):
            remaining = []
            for group_items in grouped_items.values():
                remaining.extend(group_items)

            while len(selected) < limit and remaining:
                candidate = self._best_diverse_candidate(remaining, selected, weights)
                if count("diversity.backfill_exhausted", not candidate):
                    break
                selected.append(candidate)
                remaining.remove(candidate)

        return selected

    def _best_diverse_candidate(
        self, candidates: list, selected: list, weights: DiversityWeights = DIVERSITY_WEIGHTS
    ):
        if not candidates:
            return None

        if not selected:
            return candidates[0]

        best_item = None
        best_score = float("-inf")

        for candidate in candidates:
            score = self._diversity_score(candidate, selected, weights)
            if score > best_score:
                best_score = score
                best_item = candidate

        return best_item

    def _diversity_score(
        self, candidate, selected: list, weights: DiversityWeights = DIVERSITY_WEIGHTS
    ) -> float:
        """How much this candidate is penalised for what is already selected.

        ADR-044 / #255. Three things to know about the magnitudes.

        They are no longer literals here. `weights` defaults to
        DIVERSITY_WEIGHTS, declared above, because the trace harness has to be
        able to perturb them and a copy in params.py would be a second
        definition of a number with a derivation.

        The `len(content) < 80 -> -0.08` term that stood at the top is GONE. It
        was not a distinct signal. `8d28336` introduced this function with
        `relevance = 1.0`, a constant; `8cb0f6c` added `len < 80 -> -0.1`
        against that constant, a self-contained 10% discount; and `db02670`
        then changed the baseline to `float(candidate.score)` IN THE SAME HUNK
        that retuned it to -0.08, silently re-denominating a
        fraction-of-a-constant into an absolute penalty on a composed cosine
        score. The prior already carries a measured, bounded length family
        (LEN_UNDER_50, on STRIPPED characters at threshold 50); this one was on
        RAW characters at 80, so trailing whitespace alone moved a record across
        it, and a record under both thresholds paid twice.

        The asymmetry that remains is the subject of the bound: `max_similarity`
        is a MAX and so is capped by construction, while the three
        per-neighbour terms are SUMS and grow with len(selected). At limit=6 the
        type term reaches 0.25 and the document term 1.10, against a composed
        score of roughly 0.4 to 0.8.
        """
        relevance = float(getattr(candidate, "score", 0.0))

        candidate_tokens = self._tokenize(candidate.content)
        candidate_type = getattr(candidate, "item_type", "unknown")
        candidate_metadata = getattr(candidate, "metadata", {}) or {}
        candidate_doc_id = candidate_metadata.get("doc_id")
        candidate_title = candidate_metadata.get("title")

        max_similarity = 0.0
        same_type_penalty = 0.0
        same_doc_penalty = 0.0
        same_title_penalty = 0.0

        # Bound once. This loop runs O(candidates x selected) times per pick, and
        # with div.* in the swept vector it also runs inside the trace harness's
        # sampling loop, where it was formerly a constant.
        w_type = weights.same_type
        w_doc = weights.same_doc
        w_title = weights.same_title

        for existing in selected:
            existing_tokens = self._tokenize(existing.content)
            similarity = self._jaccard_similarity(candidate_tokens, existing_tokens)
            max_similarity = max(max_similarity, similarity)

            existing_type = getattr(existing, "item_type", "unknown")
            existing_metadata = getattr(existing, "metadata", {}) or {}

            if existing_type == candidate_type:
                same_type_penalty += w_type

            if candidate_doc_id and existing_metadata.get("doc_id") == candidate_doc_id:
                same_doc_penalty += w_doc

            if candidate_title and existing_metadata.get("title") == candidate_title:
                same_title_penalty += w_title

        # One count per candidate evaluation, not per inner-loop comparison:
        # the loop runs O(candidates x selected) times per pick and the
        # per-term question is "did this fire at all for this candidate".
        #
        # These exist because the absence of them cost a measurement. #255 had
        # to discover by ad-hoc probe that no two candidates on any
        # diversity-policy query share a doc_id or a title -- so same_doc and
        # same_title have never fired on this corpus, and nothing in the
        # traffic window said so. A term that cannot fire and a term that fires
        # and does nothing are different findings, and the counters are what
        # tell them apart.
        count("diversity.penalty.same_type", same_type_penalty > 0.0)
        count("diversity.penalty.same_doc", same_doc_penalty > 0.0)
        count("diversity.penalty.same_title", same_title_penalty > 0.0)
        count("diversity.penalty.similarity", max_similarity > 0.0)

        # THE BOUND. The selection objective may move a record by at most
        # SELECTION_BAND of that record's own score, and the cap is on the TOTAL
        # rather than on each term. SELECTION_BAND above carries the derivation
        # and why the total is capped rather than the terms; ADR-044's 2026-10-02
        # amendment carries the measurement it rests on.
        #
        # The similarity coefficient is derived rather than carried. It was 0.70
        # ABSOLUTE against a composed score of roughly 0.4 to 0.8 -- at
        # max_jaccard = 1 that is up to 175% of the record, for a term whose job
        # is to separate near-duplicates. It is now SELECTION_BAND applied
        # relatively, so two identical records cost each other exactly one band
        # and partial overlap costs proportionally less.
        #
        # abs() on relevance: a composed score is non-negative in practice, but a
        # negative baseline would otherwise invert the cap into a licence.
        ceiling = abs(relevance) * SELECTION_BAND
        penalty = (
            max_similarity * ceiling * weights.similarity_share
            + same_doc_penalty
            + same_title_penalty
            + same_type_penalty
        )
        if count("diversity.penalty_capped", penalty > ceiling):
            penalty = ceiling

        return relevance - penalty

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
