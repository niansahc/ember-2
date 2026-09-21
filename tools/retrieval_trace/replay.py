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

from .compose import STAGE_DECAY, STAGE_RETRIEVAL, Composition, compose
from .params import ReplayParams
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
    delivered_refs: list[str]
    delivered_reflection_refs: list[str]

    def by_ref(self) -> dict[str, ScoredCandidate]:
        return {s.ref: s for s in self.scored}


def score_query(query: QueryTrace, params: ReplayParams | None = None) -> list[ScoredCandidate]:
    params = params or ReplayParams()
    return [
        ScoredCandidate(candidate=c, composition=compose(c, params, query.policy_name))
        for c in query.candidates
    ]


def _content_filtered(c: CandidateTrace) -> bool:
    """The filters that read content, never a score. Fixed under perturbation."""
    return bool(c.filtered_echo_or_meta or c.filtered_low_value or c.deduped_out)


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
        from src.context.service import ContextService

        shims = [_DiversityShim(s) for s in other]
        chosen = ContextService._select_diverse_memory(
            ContextService.__new__(ContextService), shims, query.memory_limit
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

    return QueryReplay(
        query_id=query.query_id,
        policy_name=query.policy_name,
        scored=scored,
        delivered_refs=[s.ref for s in profile + selected_other],
        delivered_reflection_refs=[
            s.ref for s in surviving_reflections[: query.reflection_limit]
        ],
    )


class _DiversityShim:
    """Just enough of a ContextItem for _select_diverse_memory to read."""

    __slots__ = ("scored", "memory_type", "item_type", "score", "content", "id")

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


def replay_run(run: TraceRun, params: ReplayParams | None = None) -> list[QueryReplay]:
    return [replay_query(q, params) for q in run.queries]


# ---------------------------------------------------------------------------
# Fidelity check
# ---------------------------------------------------------------------------

# Matches capture.STAGE_TOLERANCE. Anything looser would let a replay call
# itself faithful while sitting far enough from the pipeline to reorder two
# close candidates.
EXACT_TOLERANCE = 1e-12


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
            captured = scored.candidate.stage_scores[STAGE_DECAY]
            if abs(captured - scored.score) > EXACT_TOLERANCE:
                score_mismatches.append(
                    f"{query.query_id}/{scored.ref}: captured {captured!r} "
                    f"!= replayed {scored.score!r}"
                )

        if set(replay.delivered_refs) != set(query.delivered_refs):
            delivery_mismatches.append(
                f"{query.query_id}: captured {sorted(query.delivered_refs)} "
                f"!= replayed {sorted(replay.delivered_refs)}"
            )
        if set(replay.delivered_reflection_refs) != set(query.delivered_reflection_refs):
            delivery_mismatches.append(
                f"{query.query_id} (reflections): "
                f"captured {sorted(query.delivered_reflection_refs)} "
                f"!= replayed {sorted(replay.delivered_reflection_refs)}"
            )

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
    baseline: dict[str, dict[str, float]] = {}
    for query in run.queries:
        baseline[query.query_id] = {s.ref: s.score for s in replay_query(query).scored}

    defaults = run.param_defaults
    results: list[tuple[str, int]] = []
    for name in sorted(defaults):
        # +1.0 rather than a proportional nudge: some defaults are 0.0, and
        # a proportional nudge would leave those pinned and report them
        # inert for a reason that belongs to the probe, not the corpus.
        params = ReplayParams().with_overrides(**{name: defaults[name] + 1.0})
        moved = 0
        for query in run.queries:
            for scored in replay_query(query, params).scored:
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
    baseline = {q.query_id: set(replay_query(q).delivered_refs) for q in run.queries}
    results = []
    for value in values:
        params = ReplayParams().with_overrides(**{param_name: value})
        changed = 0
        for query in run.queries:
            delivered = set(replay_query(query, params).delivered_refs)
            changed += len(delivered ^ baseline[query.query_id])
        results.append((value, changed))
    return results
