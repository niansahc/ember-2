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


def delivered_sets(run: TraceRun, params: ReplayParams) -> dict[str, set[str]]:
    sets: dict[str, set[str]] = {}
    for query in run.queries:
        replay = replay_query(query, params)
        sets[query.query_id] = set(replay.delivered_refs) | {
            f"refl:{ref}" for ref in replay.delivered_reflection_refs
        }
    return sets


@dataclass
class EndpointEvaluator:
    """Evaluates both endpoints for one parameter vector, in one pass.

    The delivery endpoint is measured against the SHIPPED configuration's
    delivered set, computed once at construction, so it is a distance from
    production rather than from an arbitrary origin.
    """

    run: TraceRun
    baseline_delivery: dict[str, set[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.baseline_delivery:
            self.baseline_delivery = delivered_sets(self.run, ReplayParams())

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

            delivered = set(replay.delivered_refs) | {
                f"refl:{ref}" for ref in replay.delivered_reflection_refs
            }
            baseline = self.baseline_delivery[query.query_id]
            union = delivered | baseline
            per_query_distance.append(
                len(delivered ^ baseline) / len(union) if union else 0.0
            )

        return {
            ENDPOINT_SCORE: fmean(per_query_score) if per_query_score else 0.0,
            ENDPOINT_DELIVERY: fmean(per_query_distance) if per_query_distance else 0.0,
        }
