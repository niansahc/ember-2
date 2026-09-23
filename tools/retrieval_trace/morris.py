"""
tools/retrieval_trace/morris.py

Morris screening over the retrieval scoring parameters.

Screening, not quantification. Morris answers "which of these 79 could
plausibly matter" cheaply, so Sobol -- which costs orders of magnitude more
model evaluations -- can be pointed at the dozen that do. The outputs are:

    mu      mean elementary effect. Signed: it says which DIRECTION the
            output moves. A mu near zero alongside a large mu* means the
            effect changes sign across the space, which is itself a
            finding.
    mu*     mean ABSOLUTE elementary effect (Campolongo 2007). The ranking
            statistic. Immune to the cancellation that makes mu misleading
            for non-monotonic factors.
    sigma   standard deviation of the elementary effects. Large sigma means
            the effect depends on where in the parameter space you measure
            it -- either non-linearity or interaction with other factors.
            Morris cannot tell those two apart; that is Sobol's job, and
            large sigma is exactly the signal for sending a parameter there.

Method: r trajectories through a p-level grid on the unit hypercube, each
visiting k+1 points and changing one factor per step, so each trajectory
yields one elementary effect per factor at a cost of k+1 evaluations
(Morris 1991, with Campolongo's mu* and the standard B* construction).

Both endpoints -- the composed score and delivered-set membership --
are defined in endpoints.py and shared with the Sobol pass, so the two
methods are answering questions about the same quantities. The delivery
endpoint is a step function, so its elementary effects are lumpy and its
sigma is large almost everywhere; that is a property of the output, not
evidence of interaction, and the report says so.

Parameters with no activation anywhere in the trace are excluded from the
trajectories rather than screened at zero. Including them would spend
(k+1) evaluations per trajectory to rediscover that an unexercised
parameter does not move anything, and -- worse -- would seat them in the
same ranked table as measured zeros, which is the confusion the whole
separate section exists to prevent.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from statistics import fmean, pstdev

from .endpoints import (
    ENDPOINT_DELIVERY,
    ENDPOINT_SCORE,
    ENDPOINTS,
    EndpointEvaluator,
)
from .params import ReplayParams
from .ranges import ParameterRange, build_ranges
from .replay import EXACT_TOLERANCE, replay_query
from .schema import TraceRun

# ---------------------------------------------------------------------------
# Trajectory sampling
# ---------------------------------------------------------------------------

def sample_trajectory(k: int, levels: int, rng: random.Random) -> list[list[float]]:
    """One Morris trajectory: k+1 points, one factor changed per step.

    The standard construction. delta = p / (2(p-1)) with an even p gives a
    step that maps every grid point to another grid point, so the walk
    stays on the lattice and each factor's step is the same size wherever
    it is taken -- which is what makes elementary effects comparable across
    trajectories.
    """
    if levels % 2 != 0:
        raise ValueError(f"levels must be even for the standard step size, got {levels}")
    delta = levels / (2.0 * (levels - 1))

    # Base point: each coordinate on the lower half of the grid, so that a
    # +delta step always lands inside the cube.
    reachable = [i / (levels - 1) for i in range(levels // 2)]
    base = [rng.choice(reachable) for _ in range(k)]
    # Direction per factor, and the order they are visited in.
    directions = [rng.choice((1.0, -1.0)) for _ in range(k)]
    order = list(range(k))
    rng.shuffle(order)

    # A -1 direction starts the factor at the top of its reachable range so
    # the step still lands inside [0, 1].
    point = [
        base[i] + delta if directions[i] < 0 else base[i]
        for i in range(k)
    ]

    trajectory = [list(point)]
    for index in order:
        point[index] += delta * directions[index]
        trajectory.append(list(point))
    return trajectory


def elementary_effects(
    trajectory: list[list[float]],
    values: list[dict[str, float]],
    names: list[str],
) -> dict[str, dict[str, float]]:
    """One elementary effect per factor, per endpoint, from one trajectory."""
    effects: dict[str, dict[str, float]] = {}
    for step in range(len(trajectory) - 1):
        before, after = trajectory[step], trajectory[step + 1]
        changed = [i for i in range(len(before)) if abs(after[i] - before[i]) > 1e-15]
        if len(changed) != 1:
            raise ValueError(
                f"step {step} changed {len(changed)} factors; a Morris "
                "trajectory must change exactly one"
            )
        index = changed[0]
        delta = after[index] - before[index]
        effects[names[index]] = {
            endpoint: (values[step + 1][endpoint] - values[step][endpoint]) / delta
            for endpoint in ENDPOINTS
        }
    return effects


# ---------------------------------------------------------------------------
# Screening
# ---------------------------------------------------------------------------

@dataclass
class MorrisResult:
    name: str
    family: str
    low: float
    high: float
    default: float
    mu: dict[str, float]
    mu_star: dict[str, float]
    sigma: dict[str, float]
    effects: dict[str, list[float]]

    def interaction_candidate(self, endpoint: str) -> bool:
        """sigma exceeding mu* means the effect is not a constant slope.

        Morris cannot separate non-linearity from interaction -- both widen
        the spread of elementary effects the same way. The flag says "this
        one needs Sobol to tell which", not "this one interacts".
        """
        return self.sigma[endpoint] > self.mu_star[endpoint] > 0

    def no_effect(self, endpoint: str) -> bool:
        """Exercised, but every elementary effect on this endpoint was zero.

        A third kind of zero, distinct from both the ones the unexercised
        section is about. These parameters demonstrably move scores -- they
        are in the screened set because they do -- and still never move a
        delivered slot. The usual cause is that they lift a whole class of
        candidate uniformly: every conversation record gains the same
        constant, the order within the class is untouched, and the packet
        comes out identical. Calling that "within noise" would be wrong; it
        is an exact zero measured across every trajectory.
        """
        return self.mu_star[endpoint] == 0.0

    def distinguishable(self, endpoint: str, trajectories: int) -> bool:
        """Outside the 2 * SEM wedge, so the effect is not sampling noise."""
        if trajectories < 2:
            return self.mu_star[endpoint] > 0
        standard_error = self.sigma[endpoint] / (trajectories ** 0.5)
        return self.mu_star[endpoint] > 2 * standard_error


@dataclass
class ScreeningRun:
    trajectories: int
    levels: int
    seed: int
    evaluations: int
    screened: list[MorrisResult]
    unexercised: list[str]
    ranges: dict[str, ParameterRange]

    def ranked(self, endpoint: str) -> list[MorrisResult]:
        return sorted(self.screened, key=lambda r: -r.mu_star[endpoint])


def find_unexercised(run: TraceRun) -> list[str]:
    """Parameters no candidate in the trace activates.

    Measured, not assumed: each parameter is moved on its own and the
    composed scores are compared. A parameter that cannot move any score
    cannot move a delivered set either, since delivery is downstream of
    score, so one probe settles both endpoints.
    """
    defaults = run.param_defaults
    baseline = {
        query.query_id: {s.ref: s.score for s in replay_query(query).scored}
        for query in run.queries
    }

    unexercised: list[str] = []
    for name in sorted(defaults):
        params = ReplayParams().with_overrides(**{name: defaults[name] + 1.0})
        moved = False
        for query in run.queries:
            for scored in replay_query(query, params).scored:
                if abs(scored.score - baseline[query.query_id][scored.ref]) > EXACT_TOLERANCE:
                    moved = True
                    break
            if moved:
                break
        if not moved:
            unexercised.append(name)
    return unexercised


def screen(
    run: TraceRun,
    *,
    trajectories: int = 10,
    levels: int = 8,
    seed: int = 20260923,
    progress=None,
) -> ScreeningRun:
    """Morris screening over every exercised parameter in the trace."""
    all_ranges = build_ranges(run.param_defaults)
    unexercised = find_unexercised(run)
    names = [name for name in sorted(run.param_defaults) if name not in set(unexercised)]
    if not names:
        raise ValueError("no exercised parameters in this trace; nothing to screen")

    evaluator = EndpointEvaluator(run=run)
    rng = random.Random(seed)

    collected: dict[str, dict[str, list[float]]] = {
        name: {endpoint: [] for endpoint in ENDPOINTS} for name in names
    }
    evaluations = 0

    for index in range(trajectories):
        trajectory = sample_trajectory(len(names), levels, rng)
        values = []
        for point in trajectory:
            overrides = {
                name: all_ranges[name].to_value(coordinate)
                for name, coordinate in zip(names, point)
            }
            values.append(evaluator.evaluate(ReplayParams().with_overrides(**overrides)))
            evaluations += 1
        for name, per_endpoint in elementary_effects(trajectory, values, names).items():
            for endpoint, effect in per_endpoint.items():
                collected[name][endpoint].append(effect)
        if progress:
            progress(index + 1, trajectories, evaluations)

    screened = [
        MorrisResult(
            name=name,
            family=all_ranges[name].family,
            low=all_ranges[name].low,
            high=all_ranges[name].high,
            default=all_ranges[name].default,
            mu={e: fmean(collected[name][e]) for e in ENDPOINTS},
            mu_star={e: fmean(abs(v) for v in collected[name][e]) for e in ENDPOINTS},
            # Population standard deviation: these r effects are the whole
            # sample drawn for this factor, not an estimate of a wider one.
            sigma={e: pstdev(collected[name][e]) for e in ENDPOINTS},
            effects={e: list(collected[name][e]) for e in ENDPOINTS},
        )
        for name in names
    ]

    return ScreeningRun(
        trajectories=trajectories,
        levels=levels,
        seed=seed,
        evaluations=evaluations,
        screened=screened,
        unexercised=unexercised,
        ranges=all_ranges,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

UNEXERCISED_NOTE = (
    "These parameters have no activation anywhere in the captured trace, so "
    "no perturbation of them can change any score. They read as zero because "
    "this corpus never exercised them, NOT because they were measured and "
    "found uninfluential. Excluding them from the ranked table is deliberate: "
    "a measured zero and an unmeasurable one look identical in a sorted "
    "column and mean opposite things."
)


def format_report(screening: ScreeningRun, top: int = 0) -> str:
    lines: list[str] = []
    lines.append("Morris screening -- retrieval scoring parameters")
    lines.append(
        f"  trajectories {screening.trajectories} | levels {screening.levels} | "
        f"seed {screening.seed} | {screening.evaluations} model evaluations"
    )
    lines.append(
        f"  screened {len(screening.screened)} parameter(s); "
        f"{len(screening.unexercised)} unexercised and reported separately"
    )
    lines.append(
        "  ranges: multipliers swept over [0, 1]; additive terms over "
        "default +/- its own magnitude"
    )
    lines.append(
        "  mu* is comparable across parameters ONLY under those ranges -- "
        "they are the weighting"
    )

    for endpoint in ENDPOINTS:
        lines.append("")
        lines.append(f"  ENDPOINT: {endpoint}")
        if endpoint == ENDPOINT_DELIVERY:
            lines.append(
                "  (step function: lumpy elementary effects and large sigma are "
                "properties of the output, not evidence of interaction)"
            )
        lines.append(
            f"    {'rank':>4}  {'parameter':36} {'mu*':>10} {'mu':>10} "
            f"{'sigma':>10} {'sigma/mu*':>9}  flags"
        )
        ranked = screening.ranked(endpoint)
        flat = [r for r in ranked if r.no_effect(endpoint)]
        if flat:
            lines.append(
                f"  ({len(flat)} exercised parameter(s) move a score but never move "
                "a delivered slot -- an exact zero, not noise; see NO-EFFECT below)"
            )
            ranked = [r for r in ranked if not r.no_effect(endpoint)]
        if top:
            ranked = ranked[:top]
        for position, result in enumerate(ranked, start=1):
            mu_star = result.mu_star[endpoint]
            ratio = f"{result.sigma[endpoint] / mu_star:>9.2f}" if mu_star else f"{'-':>9}"
            flags = []
            if result.interaction_candidate(endpoint):
                flags.append("INTERACTION?")
            if not result.distinguishable(endpoint, screening.trajectories):
                flags.append("within-noise")
            lines.append(
                f"    {position:>4}  {result.name:36} {mu_star:>10.4f} "
                f"{result.mu[endpoint]:>10.4f} {result.sigma[endpoint]:>10.4f} "
                f"{ratio}  {' '.join(flags)}"
            )
        if flat:
            lines.append(f"    NO-EFFECT on {endpoint} ({len(flat)}):")
            for result in flat:
                lines.append(f"      {result.name}")

    lines.append("")
    lines.append(f"  UNEXERCISED ({len(screening.unexercised)}) -- not ranked")
    for note_line in _wrap(UNEXERCISED_NOTE, 72):
        lines.append(f"  {note_line}")
    for name in screening.unexercised:
        lines.append(f"    {name}")

    return "\n".join(lines)


@dataclass
class StabilityReport:
    """Does the ranking survive a different random draw of trajectories?

    A Morris ranking is an estimate from r samples per factor. If the top
    of the table reshuffles when the seed changes, r was too small and the
    ranking is not yet a result. This is the cheapest possible check and it
    is the one most often skipped.
    """

    endpoint: str
    top: int
    seeds: list[int]
    agreement: float             # mean pairwise overlap of the top-N sets
    max_displacement: int        # worst rank movement among the union of tops

    def stable(self, threshold: float = 0.8) -> bool:
        return self.agreement >= threshold


def rank_stability(
    screenings: list[ScreeningRun], endpoint: str, top: int = 10
) -> StabilityReport:
    tops = [
        [r.name for r in screening.ranked(endpoint)[:top]] for screening in screenings
    ]
    overlaps = []
    for i in range(len(tops)):
        for j in range(i + 1, len(tops)):
            overlaps.append(len(set(tops[i]) & set(tops[j])) / top)

    positions: dict[str, list[int]] = {}
    for screening in screenings:
        order = [r.name for r in screening.ranked(endpoint)]
        for name in set().union(*[set(t) for t in tops]):
            positions.setdefault(name, []).append(order.index(name))

    return StabilityReport(
        endpoint=endpoint,
        top=top,
        seeds=[s.seed for s in screenings],
        agreement=fmean(overlaps) if overlaps else 1.0,
        max_displacement=max(
            (max(p) - min(p) for p in positions.values()), default=0
        ),
    )


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        if sum(len(w) + 1 for w in current) + len(word) > width:
            lines.append(" ".join(current))
            current = []
        current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def to_dict(screening: ScreeningRun) -> dict:
    """Serializable results. Parameter names and numbers only -- no vault data."""
    return {
        "trajectories": screening.trajectories,
        "levels": screening.levels,
        "seed": screening.seed,
        "evaluations": screening.evaluations,
        "unexercised": list(screening.unexercised),
        "unexercised_note": UNEXERCISED_NOTE,
        "ranges": {
            name: {"family": r.family, "low": r.low, "high": r.high, "default": r.default}
            for name, r in screening.ranges.items()
        },
        "screened": [
            {
                "name": r.name,
                "family": r.family,
                "mu": r.mu,
                "mu_star": r.mu_star,
                "sigma": r.sigma,
                "interaction_candidate": {
                    e: r.interaction_candidate(e) for e in ENDPOINTS
                },
                "no_effect": {e: r.no_effect(e) for e in ENDPOINTS},
                "distinguishable": {
                    e: r.distinguishable(e, screening.trajectories) for e in ENDPOINTS
                },
            }
            for r in screening.screened
        ],
    }
