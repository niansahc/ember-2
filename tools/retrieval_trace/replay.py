"""
tools/retrieval_trace/replay.py

Offline replay: recompute scores, and the delivered set, from a stored trace
under an arbitrary parameter vector. Touches no vault, no store, no
retriever, and no model.

What replay CAN move: every scoring constant in params.py, and the
per-policy weights. What it CANNOT move: which candidates were retrieved,
and which of them the content-based filters removed. Those are properties
of the corpus and of predicates over content, not of the parameters, so
they are carried as fixed outcomes in the trace. A sensitivity result from
this harness is therefore a statement about scoring and selection GIVEN the
candidate pool, which is the honest scope and should be quoted that way.

The selection replay is faithful rather than approximate because the two
score-dependent steps are the real ones: the ADR-018 type gate's min_score
half is re-applied against the perturbed retrieval score, and diversity
selection calls ContextService._select_diverse_memory itself. Only the
content-based filters -- echo, meta, low value, dedup -- are read from the
trace, and none of those reads a score.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.context.render_window import (
    rendered_memory_window,
    rendered_reflection_window,
)

from .compose import STAGE_FINAL, STAGE_RETRIEVAL, Composition, compose
from .params import SELECTION_ONLY_PARAMS, ReplayParams
from .schema import CHANNEL_REFLECTION, CandidateTrace, QueryTrace, TraceRun


@dataclass
class ScoredCandidate:
    candidate: CandidateTrace
    composition: Composition

    @property
    def ref(self) -> str:
        return self.candidate.ref

    @property
    def score(self) -> float:
        return self.composition.final


@dataclass
class QueryReplay:
    query_id: str
    policy_name: str
    scored: list[ScoredCandidate]
    rendered_refs: list[str]
    rendered_reflection_refs: list[str]

    def by_ref(self) -> dict[str, ScoredCandidate]:
        return {s.ref: s for s in self.scored}


def score_query(query: QueryTrace, params: ReplayParams | None = None) -> list[ScoredCandidate]:
    params = params or ReplayParams()
    return [
        ScoredCandidate(candidate=c, composition=compose(c, params, query.policy_name))
        for c in query.candidates
    ]


def _content_filtered(c: CandidateTrace) -> bool:
    """The filters that read metadata or content, never a score.

    Fixed under perturbation, which is exactly why the role predicate belongs
    here rather than anywhere else: it reads the authorship column and no
    number, so no scoring parameter can move it. Omitting it was a real
    fidelity gap -- replay reproduced every score and still delivered
    assistant turns the shipped pipeline had already dropped.
    """
    return bool(
        c.filtered_echo_or_meta
        or c.filtered_low_value
        or c.deduped_out
        or c.excluded_by_role
    )


def _gate_survivor(scored: ScoredCandidate, query: QueryTrace) -> bool:
    """The gates that a scoring parameter CAN move."""
    c = scored.candidate
    if c.memory_type == "profile":
        # Profile bypasses the type gate and the relevance gate by design.
        return True
    if c.relevance_gated_out:
        return False
    if not c.type_eligible:
        return False
    # The min_score half of the ADR-018 gate, re-applied against the
    # PERTURBED retrieval score. This is the one place a scoring parameter
    # changes membership rather than only order, so it has to move with the
    # score instead of being carried as a fixed outcome.
    return scored.composition.stage_scores[STAGE_RETRIEVAL] >= query.min_score


def replay_query(query: QueryTrace, params: ReplayParams | None = None) -> QueryReplay:
    """Re-score and re-select one query under `params`."""
    params = params or ReplayParams()
    scored = score_query(query, params)

    memory = [s for s in scored if s.candidate.channel != CHANNEL_REFLECTION]
    reflections = [s for s in scored if s.candidate.channel == CHANNEL_REFLECTION]

    gated = [s for s in memory if _gate_survivor(s, query)]
    survivors = [s for s in gated if not _content_filtered(s.candidate)]
    # build_context's fallback: when the content filters take everything,
    # the unfiltered ranked list is used instead of an empty packet.
    if not survivors:
        survivors = gated
    survivors.sort(key=lambda s: s.score, reverse=True)

    profile = [s for s in survivors if s.candidate.memory_type == "profile"]
    other = [s for s in survivors if s.candidate.memory_type != "profile"]

    if query.diversity:
        # The real round-robin, not an approximation of it. It reads
        # item.memory_type and item.score off objects, so a light shim is
        # enough to hand it scored candidates.
        from src.context.service import ContextService, DiversityWeights

        # Refuse rather than degrade. `_diversity_score` reads content for the
        # Jaccard term, so a content-free trace replays selection as
        # round-robin-on-score and says nothing about it -- the shim's
        # `content or ""` turns "cannot replay this" into "replayed it wrongly".
        # The CLI carries content by default precisely because of this; the
        # library default does not, so the check is worth having.
        if any(s.candidate.content is None for s in other):
            raise ValueError(
                f"{query.query_id}: diversity replay needs candidate content, and "
                "this trace was captured without it. Recapture without "
                "--no-content; a content-free trace can replay scores exactly but "
                "not selection."
            )

        shims = [_DiversityShim(s) for s in other]
        # The sampled weights, or the parameters move nothing and their ST is a
        # measurement of the harness rather than of the pipeline.
        chosen = ContextService._select_diverse_memory(
            ContextService.__new__(ContextService),
            shims,
            query.memory_limit,
            DiversityWeights(
                similarity_share=params["div.similarity_share"],
                same_type=params["div.same_type"],
                same_doc=params["div.same_doc"],
                same_title=params["div.same_title"],
            ),
        )
        selected_other = [shim.scored for shim in chosen]
    else:
        selected_other = other[: query.memory_limit]

    # Reflections pass through the same ADR-018 gate in build_context, and
    # on most policies that is what empties the channel: the gate compares
    # min_score against a Jaccard overlap, which is on a different scale
    # from every other score in the system.
    surviving_reflections = [
        s
        for s in reflections
        if _gate_survivor(s, query) and not _content_filtered(s.candidate)
    ]
    surviving_reflections.sort(key=lambda s: s.score, reverse=True)

    # Two truncations, in pipeline order. The service limit first -- 4 to 6
    # memory records, 1 to 3 reflections, per policy -- and then the window the
    # PROMPT applies, which is 4 and 1 regardless of policy.
    #
    # Only the second one is new here, and it is the whole point of the change.
    # Stopping at the service limit meant the delivery endpoint measured a
    # candidate set rather than what the model receives, and was blind to
    # movement across the 4/5 boundary: both candidates stay in the packet, so
    # set membership is unchanged and the measured distance is zero for the one
    # reordering that decides whether a record is seen at all.
    #
    # The window comes from src/context/render_window.py, which the prompt
    # builder renders with and capture checks against. CandidateTrace carries
    # memory_type and ref, so it is passed straight in -- no shim, and no second
    # copy of the slice.
    rendered_profile, rendered_other = rendered_memory_window(
        [s.candidate for s in profile + selected_other]
    )
    rendered_reflections = rendered_reflection_window(
        [s.candidate for s in surviving_reflections[: query.reflection_limit]]
    )

    return QueryReplay(
        query_id=query.query_id,
        policy_name=query.policy_name,
        scored=scored,
        rendered_refs=[c.ref for c in rendered_profile + rendered_other],
        rendered_reflection_refs=[c.ref for c in rendered_reflections],
    )


class _DiversityShim:
    """Just enough of a ContextItem for _select_diverse_memory to read."""

    __slots__ = (
        "scored", "memory_type", "item_type", "score", "content", "id", "metadata",
    )

    def __init__(self, scored: ScoredCandidate) -> None:
        self.scored = scored
        self.memory_type = scored.candidate.memory_type
        # _select_diverse_memory groups on item_type, not memory_type. They
        # differ often enough (the retriever sets both, but the ablation
        # path relabels one of them) that reading the wrong one would
        # silently collapse the round-robin into a single group.
        self.item_type = scored.candidate.item_type
        self.score = scored.score
        self.content = scored.candidate.content or ""
        self.id = scored.candidate.ref
        # #255. Without this slot `_diversity_score`'s
        # `getattr(candidate, "metadata", {}) or {}` returned {} for every
        # candidate, so `same_doc_penalty` (0.22/item, the uncapped one) and
        # `same_title_penalty` were pinned to zero in replay -- not for this
        # corpus, but structurally, for any trace. The hashed group ids carry
        # exactly what the selector tests, which is equality.
        self.metadata = {
            "doc_id": scored.candidate.doc_group,
            "title": scored.candidate.title_group,
        }


def replay_run(run: TraceRun, params: ReplayParams | None = None) -> list[QueryReplay]:
    return [replay_query(q, params) for q in run.queries]


def delivery_baseline(run: TraceRun) -> dict[str, set[str]]:
    """The unperturbed rendered memory set per query, computed once.

    A probe that recomputes this inside its own parameter loop pays a full
    selection pass per (parameter x range end x query) to rediscover a value
    that does not depend on the parameter. `sweep` already hoists it; the two
    one-at-a-time probes did not.
    """
    return {q.query_id: set(replay_query(q).rendered_refs) for q in run.queries}


def delivery_moved(
    run: TraceRun,
    name: str,
    ends: tuple[float, ...],
    *,
    baseline: dict[str, set[str]],
    stop_early: bool = False,
) -> int:
    """How many rendered refs a parameter moves, perturbed alone to each `end`.

    ONE definition of the delivery probe. morris.find_unexercised and
    parameter_coverage both need it -- the first to give a score-flat parameter a
    second chance, the second to report the selection family's coverage -- and
    writing it twice is how the two probes drifted apart before: the range-aware
    nudge was migrated into one and not the other, and #255 found the cost.
    `stop_early` is the only difference between the callers that is real, since
    find_unexercised needs a boolean and can quit on the first movement while
    parameter_coverage needs the count.

    Non-diversity queries are skipped. Their rendered set is a pure function of
    the composed scores, so a parameter that moves no score cannot move their
    delivery, and a parameter that does move a score has no business in this
    probe.
    """
    moved = 0
    for end in ends:
        params = ReplayParams().with_overrides(**{name: end})
        for query in run.queries:
            if not query.diversity:
                continue
            after = set(replay_query(query, params).rendered_refs)
            moved += len(baseline[query.query_id] ^ after)
            if moved and stop_early:
                return moved
    return moved


# ---------------------------------------------------------------------------
# Fidelity check
# ---------------------------------------------------------------------------

# Matches capture.STAGE_TOLERANCE. Anything looser would let a replay call
# itself faithful while sitting far enough from the pipeline to reorder two
# close candidates.
EXACT_TOLERANCE = 1e-12


def render_mismatches(query: QueryTrace, replay: QueryReplay) -> list[str]:
    """Where the model's rendered set disagrees with the pipeline's, per channel.

    One comparison, two callers. capture._validate_render refuses to write a
    trace that fails it, and check_fidelity reports it over a whole run. Those
    are different responses to the same invariant, and when the invariant was
    written out twice the two sites drifted in what they reported.

    Sets, not sequences: membership is what the delivery endpoint measures, and
    the prompt renders profile and non-profile in separate sections anyway, so
    order within the render is not a claim this makes.
    """
    mismatches: list[str] = []
    for channel, modelled_refs, captured_refs in (
        ("memory", replay.rendered_refs, query.rendered_refs),
        ("reflection", replay.rendered_reflection_refs, query.rendered_reflection_refs),
    ):
        modelled, captured = set(modelled_refs), set(captured_refs)
        if modelled != captured:
            mismatches.append(
                f"{query.query_id} ({channel}): model rendered {sorted(modelled)}, "
                f"pipeline rendered {sorted(captured)} "
                f"(only in model: {sorted(modelled - captured)}; "
                f"only in pipeline: {sorted(captured - modelled)})"
            )
    return mismatches


@dataclass
class FidelityReport:
    candidates_checked: int
    score_mismatches: list[str]
    delivery_mismatches: list[str]

    @property
    def exact(self) -> bool:
        return not self.score_mismatches and not self.delivery_mismatches

    def summary(self) -> str:
        return (
            f"{self.candidates_checked} candidate(s) checked; "
            f"{len(self.score_mismatches)} score mismatch(es); "
            f"{len(self.delivery_mismatches)} delivery mismatch(es)"
        )


def check_fidelity(run: TraceRun) -> FidelityReport:
    """Replay at shipped defaults and compare against what was captured.

    Two assertions, not one. The scores must reproduce, and so must the
    delivered set -- a harness that gets every number right and then selects
    a different five records is not replaying the pipeline, and the
    difference would be invisible if only scores were checked.
    """
    score_mismatches: list[str] = []
    delivery_mismatches: list[str] = []
    checked = 0

    for query in run.queries:
        replay = replay_query(query)
        for scored in replay.scored:
            checked += 1
            captured = scored.candidate.stage_scores[STAGE_FINAL]
            if abs(captured - scored.score) > EXACT_TOLERANCE:
                score_mismatches.append(
                    f"{query.query_id}/{scored.ref}: captured {captured!r} "
                    f"!= replayed {scored.score!r}"
                )

        delivery_mismatches.extend(render_mismatches(query, replay))

    return FidelityReport(
        candidates_checked=checked,
        score_mismatches=score_mismatches,
        delivery_mismatches=delivery_mismatches,
    )


def parameter_coverage(run: TraceRun) -> list[tuple[str, int]]:
    """How many candidates each parameter can move, by perturbing it alone.

    Run this BEFORE a sensitivity pass, not after. A parameter no candidate
    activates has a Sobol index of exactly zero, and that zero means "this
    corpus never exercised it" -- not "this parameter does not matter",
    which is how it will be read if nobody checks. The distinction decides
    whether a term is dead weight or merely untested here.

    Returns (name, candidates_moved) for every parameter, ascending, so the
    inert ones are the first thing the reader sees.
    """
    # score_query, not replay_query: this function reads scores and nothing
    # else. replay_query additionally runs the survivor gates, the content
    # filters, two sorts, the profile partition and -- on a diversity policy --
    # a per-candidate shim through the real _select_diverse_memory, all of which
    # was discarded. At 38 parameters x 36 queries that was ~1400 needless
    # selection passes per coverage run.
    baseline: dict[str, dict[str, float]] = {}
    for query in run.queries:
        baseline[query.query_id] = {s.ref: s.score for s in score_query(query)}

    from .ranges import range_for

    defaults = run.param_defaults
    # One pass, reused by every selection-stage parameter below.
    rendered_baseline = (
        delivery_baseline(run)
        if SELECTION_ONLY_PARAMS & set(defaults)
        else {}
    )
    results: list[tuple[str, int]] = []
    for name in sorted(defaults):
        # Range-aware, not a flat +1.0.
        #
        # +1.0 was right while every parameter was an additive term near zero:
        # some defaults ARE 0.0, and a proportional nudge would leave those
        # pinned and report them inert for a reason belonging to the probe
        # rather than the corpus. Under ADR-044 most of the vector is
        # multipliers near 1.0, where +1.0 doubles the parameter and sweeps it
        # far outside the bound the contract asserts -- so a bounded term
        # would be probed over territory it can never occupy, and could read
        # as moving candidates it cannot actually move.
        #
        # Each parameter is nudged to the far end of its own declared range
        # instead, which is the largest change the contract permits it and
        # therefore the right question to ask of it.
        spread = range_for(name, defaults[name])
        nudged = spread.high if defaults[name] < spread.high else spread.low

        if name in SELECTION_ONLY_PARAMS:
            # The selection-stage family cannot be probed on scores. #255's
            # parameters change which candidates are SELECTED and leave every
            # composed score untouched, so the score probe below would report
            # all four inert -- a zero belonging to the probe, which is exactly
            # the confusion this function exists to prevent.
            #
            # So they are probed on the rendered set, which costs the selection
            # pass the comment above avoids. Only this family pays it: four
            # parameters rather than forty. Which parameters those are is read
            # off params.SELECTION_ONLY_PARAMS rather than off their name
            # prefix, so the fact lives where the parameters are declared.
            results.append(
                (
                    name,
                    delivery_moved(
                        run, name, (nudged,), baseline=rendered_baseline
                    ),
                )
            )
            continue

        params = ReplayParams().with_overrides(**{name: nudged})
        moved = 0
        for query in run.queries:
            for scored in score_query(query, params):
                if abs(scored.score - baseline[query.query_id][scored.ref]) > EXACT_TOLERANCE:
                    moved += 1
        results.append((name, moved))
    return sorted(results, key=lambda pair: (pair[1], pair[0]))


def sweep(
    run: TraceRun,
    param_name: str,
    values: list[float],
) -> list[tuple[float, int]]:
    """One-at-a-time sweep: how many delivered slots move as one parameter moves.

    A blunt instrument on purpose -- it exists so the harness can be
    exercised end to end without pulling in a sensitivity library. The real
    Sobol pass consumes replay_run() directly.
    """
    baseline = {q.query_id: set(replay_query(q).rendered_refs) for q in run.queries}
    results = []
    for value in values:
        params = ReplayParams().with_overrides(**{param_name: value})
        changed = 0
        for query in run.queries:
            delivered = set(replay_query(query, params).rendered_refs)
            changed += len(delivered ^ baseline[query.query_id])
        results.append((value, changed))
    return results
