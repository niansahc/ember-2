"""
tools/retrieval_trace/endpoints.py

The two scalar outputs a sensitivity pass measures, shared by Morris
screening and Sobol decomposition so both are answering questions about
the same quantities.

    score       mean composed score across candidates, query-balanced.
                Continuous and well behaved, which is what makes the
                variance decomposition meaningful -- but RANK-INSENSITIVE.
                A parameter that lifts every candidate equally moves this
                and changes nothing anybody would see. Morris measured 19
                exercised parameters that do exactly that.
    delivery    Jaccard distance between the delivered set and the shipped
                configuration's delivered set, averaged over queries. This
                is the endpoint that corresponds to different records
                reaching the model. It is a step function, so its variance
                is concentrated in a few jumps and its estimators are
                noisier at the same sample size.

Both are computed from one replay pass, so an evaluation costs the same
whether one endpoint is wanted or both.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import fmean

from .params import ReplayParams
from .replay import replay_query
from .schema import TraceRun

ENDPOINT_SCORE = "score"
ENDPOINT_DELIVERY = "delivery"
ENDPOINTS: tuple[str, ...] = (ENDPOINT_SCORE, ENDPOINT_DELIVERY)

# ENDPOINT_DELIVERY keeps its string. It is a key in saved Sobol artefacts and in
# no_solo_delivery_effect, so renaming it would break every stored result for a
# vocabulary gain. What it MEASURES changed in #227 -- the rendered set, not the
# context packet -- and results taken before that are refused by
# sobol.load_results on the trace_schema_version stamp rather than silently
# re-rendered.


def rendered_keys(replay) -> set[str]:
    """Both channels in one key space, with reflections prefixed.

    One definition, because the baseline and the per-sample set must be computed
    in the SAME key space or the Jaccard distance between them is silently
    measuring nothing. It was written out twice.
    """
    return set(replay.rendered_refs) | {
        f"refl:{ref}" for ref in replay.rendered_reflection_refs
    }


def rendered_sets(run: TraceRun, params: ReplayParams) -> dict[str, set[str]]:
    return {
        query.query_id: rendered_keys(replay_query(query, params))
        for query in run.queries
    }


@dataclass
class EndpointEvaluator:
    """Evaluates both endpoints for one parameter vector, in one pass.

    The delivery endpoint is measured against the SHIPPED configuration's
    delivered set, computed once at construction, so it is a distance from
    production rather than from an arbitrary origin.
    """

    run: TraceRun
    baseline_rendered: dict[str, set[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.baseline_rendered:
            self.baseline_rendered = rendered_sets(self.run, ReplayParams())

    def evaluate(self, params: ReplayParams) -> dict[str, float]:
        per_query_score: list[float] = []
        per_query_distance: list[float] = []

        for query in self.run.queries:
            replay = replay_query(query, params)
            if replay.scored:
                # Query-balanced: a query that happened to retrieve more
                # candidates must not weigh more in the mean than one that
                # retrieved fewer.
                per_query_score.append(fmean(s.score for s in replay.scored))

            rendered = rendered_keys(replay)
            baseline = self.baseline_rendered[query.query_id]
            union = rendered | baseline
            per_query_distance.append(
                len(rendered ^ baseline) / len(union) if union else 0.0
            )

        return {
            ENDPOINT_SCORE: fmean(per_query_score) if per_query_score else 0.0,
            ENDPOINT_DELIVERY: fmean(per_query_distance) if per_query_distance else 0.0,
        }
