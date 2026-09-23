"""
tools/retrieval_trace/sobol.py

Sobol variance decomposition over the retrieval scoring parameters.

Morris ranked them; this quantifies them. Where Morris reports "sigma is
large, something non-additive is happening here", Sobol says how much of
the output variance each parameter owns alone (S1), how much it owns once
every interaction it participates in is counted (ST), and which specific
pairs carry the difference (S2).

    S1_i       first order. The variance removed by learning x_i alone.
    ST_i       total order. S1_i plus every interaction involving x_i.
    ST_i - S1_i  the interaction share. This is the number worth reading:
               a parameter with ST ~ S1 is an independent lever and can be
               retuned on its own; a parameter with ST much larger than S1
               cannot, because its effect depends on what the others are
               set to.
    S2_ij      second order, the pairwise part of that difference.

Estimators: Saltelli's A/B/AB/BA design at N(2k+2) evaluations, with
Jansen's total-order estimator and Saltelli 2010's first-order estimator --
the pairing that behaves best at small N, which matters here because every
evaluation is a full replay of the trace.

    S1_i  = mean( f(B) * (f(AB_i) - f(A)) ) / V
    ST_i  = mean( (f(A) - f(AB_i))^2 ) / 2V
    S2_ij = mean( f(BA_i) * f(AB_j) - f(A) * f(B) ) / V - S1_i - S1_j

These are estimators, not identities. At finite N they can and do produce
small negative values for indices that are truly zero; that is sampling
noise, not a negative variance contribution, and the report prints them
rather than clipping so the noise floor stays visible.

One caveat about the intervals, since they are the basis for every "is
this real" judgement here. The bootstrap resamples ROWS of the sample and
so assumes the rows are exchangeable and independent. A scrambled Sobol'
sequence is neither: it is stratified, which is the whole reason it
converges faster than plain Monte Carlo. Bootstrapping it anyway is the
conventional practice (SALib does the same) and errs in the safe
direction -- the interval is priced as though the sample were iid, so it
is wider than the estimator's true spread, not narrower. Read the widths
as conservative, and read the sample size extrapolated from them as
pessimistic.

Sample size is NOT fixed in advance. The pass doubles N, reusing every
evaluation already made (the sample stream is extensible, so the first N
rows of the 2N design are the N design), and stops when the bootstrap
confidence intervals on the top of the ST ranking are narrow enough. The
report states the N that was needed rather than the N that was assumed.

Ranges are the same convention as the Morris pass (ranges.py): multipliers
swept over [0, 1], additive constants over their shipped value plus or
minus their own magnitude. Sobol indices are dimensionless variance
shares, so unlike mu* they do not carry the range's units -- but they do
still describe variance induced BY THOSE RANGES, and a parameter given a
wider range will own more of it. The convention is restated on every
report for the same reason it is in the Morris one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .endpoints import ENDPOINT_DELIVERY, ENDPOINTS, EndpointEvaluator
from .params import ReplayParams
from .ranges import ParameterRange, build_ranges
from .schema import TraceRun

# Bootstrap resamples for the confidence intervals. 1000 is enough for a
# 95% percentile interval to be stable to the third decimal, and it costs
# nothing: the bootstrap resamples stored OUTPUTS, it does not re-evaluate
# the model.
BOOTSTRAP_RESAMPLES = 1000
CONFIDENCE = 0.95


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

class SampleStream:
    """An extensible stream of points in [0, 1]^(2k).

    Extensible is the requirement, not a nicety. The convergence loop
    doubles N and must be able to reuse every evaluation it has already
    paid for, which only works if the first N rows of the 2N sample are
    the N sample.

    Prefers a scrambled Sobol' sequence, because a low-discrepancy sequence
    reaches a given interval width in far fewer samples than plain Monte
    Carlo, and here each sample costs 2k+2 full replays. That needs scipy,
    which is present in this environment but is NOT in requirements.txt, so
    it cannot be assumed.

    The sampler is therefore selectable and always recorded, never silently
    inferred:

        "auto"    Sobol' if scipy is importable, else numpy. Records which.
        "sobol"   Sobol', or raise. For a run whose numbers will be quoted.
        "random"  numpy, always available. For anything that must behave
                  identically on every machine.

    "auto" used to be the only behaviour, and it made accuracy a property
    of the machine: the same code at the same N produced QMC-quality
    numbers here and Monte-Carlo-quality numbers in CI, where scipy is
    absent. Tests calibrated against one silently failed on the other. The
    two samplers converge to the same indices but not at the same N, so
    which one ran is part of the result, not an implementation detail.
    """

    def __init__(self, dimensions: int, seed: int, sampler: str = "auto") -> None:
        self.dimensions = dimensions
        self.seed = seed
        self._rows = np.empty((0, dimensions))

        if sampler not in {"auto", "sobol", "random"}:
            raise ValueError(f"unknown sampler {sampler!r}")

        engine = None
        if sampler in {"auto", "sobol"}:
            try:
                from scipy.stats import qmc

                engine = qmc.Sobol(d=dimensions, scramble=True, seed=seed)
                self.sampler = "scipy.qmc.Sobol(scrambled)"
            except ImportError:
                if sampler == "sobol":
                    raise RuntimeError(
                        "sampler='sobol' requires scipy, which is not installed. "
                        "Use 'random' and expect to need roughly an order of "
                        "magnitude more samples for the same interval width."
                    ) from None

        if engine is None:
            engine = np.random.default_rng(seed)
            self.sampler = "numpy.default_rng"
        self._engine = engine

    def take(self, n: int) -> np.ndarray:
        """The first n rows, drawing more only if we do not have them yet."""
        if n > len(self._rows):
            wanted = n - len(self._rows)
            if hasattr(self._engine, "random_base2"):
                new = self._engine.random(wanted)
            else:
                new = self._engine.random((wanted, self.dimensions))
            self._rows = np.vstack([self._rows, new]) if len(self._rows) else new
        return self._rows[:n]


def saltelli_matrices(points: np.ndarray, k: int) -> dict:
    """Split a 2k-wide sample into the A, B, AB_i and BA_i design."""
    a = points[:, :k]
    b = points[:, k:]
    ab = []
    ba = []
    for i in range(k):
        ab_i = a.copy()
        ab_i[:, i] = b[:, i]
        ab.append(ab_i)
        ba_i = b.copy()
        ba_i[:, i] = a[:, i]
        ba.append(ba_i)
    return {"A": a, "B": b, "AB": ab, "BA": ba}


# ---------------------------------------------------------------------------
# Estimators
# ---------------------------------------------------------------------------

def first_order(fa: np.ndarray, fb: np.ndarray, fab: np.ndarray, variance: float) -> float:
    """Saltelli 2010."""
    if variance <= 0:
        return 0.0
    return float(np.mean(fb * (fab - fa)) / variance)


def total_order(fa: np.ndarray, fab: np.ndarray, variance: float) -> float:
    """Jansen 1999."""
    if variance <= 0:
        return 0.0
    return float(np.mean((fa - fab) ** 2) / (2.0 * variance))


def second_order(
    fa: np.ndarray,
    fb: np.ndarray,
    fab_i: np.ndarray,
    fab_j: np.ndarray,
    fba_i: np.ndarray,
    variance: float,
    s1_i: float,
    s1_j: float,
) -> float:
    """Saltelli 2002, closed second order minus the two first orders."""
    if variance <= 0:
        return 0.0
    closed = float(np.mean(fba_i * fab_j - fa * fb) / variance)
    return closed - s1_i - s1_j


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class Interval:
    low: float
    high: float

    @property
    def half_width(self) -> float:
        return (self.high - self.low) / 2.0


@dataclass
class ParameterIndices:
    name: str
    s1: float
    st: float
    s1_ci: Interval
    st_ci: Interval

    @property
    def interaction_share(self) -> float:
        """ST - S1: the part of this parameter's influence that is not its own.

        Reported unclipped. A small negative value means the two estimators
        disagreed within their noise, which is information about the sample
        size; clipping it to zero would hide exactly that.
        """
        return self.st - self.s1

    def additive(self, tolerance: float = 0.01) -> bool:
        return abs(self.interaction_share) <= tolerance


@dataclass
class PairIndex:
    first: str
    second: str
    s2: float
    s2_ci: Interval | None = None

    def significant(self) -> bool:
        """Nonzero at the stated confidence, rather than merely large."""
        return self.s2_ci is not None and self.s2_ci.low > 0


@dataclass
class EndpointIndices:
    endpoint: str
    variance: float
    parameters: list[ParameterIndices]
    pairs: list[PairIndex]

    def ranked(self) -> list[ParameterIndices]:
        return sorted(self.parameters, key=lambda p: -p.st)

    def by_name(self) -> dict[str, ParameterIndices]:
        return {p.name: p for p in self.parameters}

    def top_pairs(self, limit: int = 15) -> list[PairIndex]:
        return sorted(self.pairs, key=lambda p: -abs(p.s2))[:limit]

    def pair(self, first: str, second: str) -> PairIndex | None:
        for candidate in self.pairs:
            if {candidate.first, candidate.second} == {first, second}:
                return candidate
        return None


@dataclass
class SobolRun:
    names: list[str]
    samples: int
    evaluations: int
    seed: int
    sampler: str
    endpoints: dict[str, EndpointIndices]
    unexercised: list[str]
    ranges: dict[str, ParameterRange]
    convergence: list[dict] = field(default_factory=list)
    st_ci_target: float = 0.0
    # Exercised, but cannot move the delivered set acting alone. A marker on
    # the delivery table, not an exclusion -- see find_no_delivery_effect.
    no_solo_delivery_effect: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------

class SobolAnalysis:
    """Holds the evaluation cache so N can double without re-paying."""

    def __init__(
        self,
        run: TraceRun,
        names: list[str],
        *,
        seed: int = 20260923,
        pair_bootstrap: int = 25,
        sampler: str = "auto",
    ) -> None:
        self.run = run
        self.names = names
        self.seed = seed
        self.pair_bootstrap = pair_bootstrap
        self.ranges = build_ranges(run.param_defaults)
        self.evaluator = EndpointEvaluator(run=run)
        self.stream = SampleStream(
            dimensions=2 * len(names), seed=seed, sampler=sampler
        )
        # matrix key -> endpoint -> outputs, grown in place as N doubles.
        self._outputs: dict[str, dict[str, list[float]]] = {}
        self.evaluations = 0

    # -- evaluation ------------------------------------------------------

    def _params_for(self, row: np.ndarray) -> ReplayParams:
        overrides = {
            name: self.ranges[name].to_value(float(coordinate))
            for name, coordinate in zip(self.names, row)
        }
        return ReplayParams().with_overrides(**overrides)

    def _evaluate_matrix(self, key: str, matrix: np.ndarray, progress=None) -> dict:
        cached = self._outputs.setdefault(key, {e: [] for e in ENDPOINTS})
        have = len(cached[ENDPOINTS[0]])
        for row in matrix[have:]:
            values = self.evaluator.evaluate(self._params_for(row))
            for endpoint in ENDPOINTS:
                cached[endpoint].append(values[endpoint])
            self.evaluations += 1
            if progress and self.evaluations % 2000 == 0:
                progress(self.evaluations)
        return {e: np.asarray(cached[e][: len(matrix)]) for e in ENDPOINTS}

    def outputs_for(self, n: int, progress=None) -> dict:
        points = self.stream.take(n)
        design = saltelli_matrices(points, len(self.names))
        outputs = {
            "A": self._evaluate_matrix("A", design["A"], progress),
            "B": self._evaluate_matrix("B", design["B"], progress),
            "AB": [
                self._evaluate_matrix(f"AB{i}", design["AB"][i], progress)
                for i in range(len(self.names))
            ],
            "BA": [
                self._evaluate_matrix(f"BA{i}", design["BA"][i], progress)
                for i in range(len(self.names))
            ],
        }
        return outputs

    # -- estimation ------------------------------------------------------

    def indices(self, n: int, progress=None) -> dict[str, EndpointIndices]:
        outputs = self.outputs_for(n, progress)
        rng = np.random.default_rng(self.seed + 1)
        resamples = rng.integers(0, n, size=(BOOTSTRAP_RESAMPLES, n))

        results: dict[str, EndpointIndices] = {}
        for endpoint in ENDPOINTS:
            fa = outputs["A"][endpoint]
            fb = outputs["B"][endpoint]
            fab = [outputs["AB"][i][endpoint] for i in range(len(self.names))]
            fba = [outputs["BA"][i][endpoint] for i in range(len(self.names))]
            variance = float(np.var(np.concatenate([fa, fb]), ddof=1))

            parameters: list[ParameterIndices] = []
            s1_values: list[float] = []
            for i, name in enumerate(self.names):
                s1 = first_order(fa, fb, fab[i], variance)
                st = total_order(fa, fab[i], variance)
                s1_values.append(s1)
                parameters.append(
                    ParameterIndices(
                        name=name,
                        s1=s1,
                        st=st,
                        s1_ci=_bootstrap_ci(
                            lambda idx, i=i: first_order(
                                fa[idx], fb[idx], fab[i][idx], _variance(fa[idx], fb[idx])
                            ),
                            resamples,
                        ),
                        st_ci=_bootstrap_ci(
                            lambda idx, i=i: total_order(
                                fa[idx], fab[i][idx], _variance(fa[idx], fb[idx])
                            ),
                            resamples,
                        ),
                    )
                )

            pairs = [
                PairIndex(
                    first=self.names[i],
                    second=self.names[j],
                    s2=second_order(
                        fa, fb, fab[i], fab[j], fba[i], variance, s1_values[i], s1_values[j]
                    ),
                )
                for i in range(len(self.names))
                for j in range(i + 1, len(self.names))
            ]
            # Bootstrapping all k(k-1)/2 pairs would cost more than the model
            # evaluations did. The point estimates are all reported; intervals
            # go on the largest, which are the only ones anyone will act on.
            for pair in sorted(pairs, key=lambda p: -abs(p.s2))[: self.pair_bootstrap]:
                i = self.names.index(pair.first)
                j = self.names.index(pair.second)
                pair.s2_ci = _bootstrap_ci(
                    lambda idx, i=i, j=j: second_order(
                        fa[idx],
                        fb[idx],
                        fab[i][idx],
                        fab[j][idx],
                        fba[i][idx],
                        _variance(fa[idx], fb[idx]),
                        first_order(fa[idx], fb[idx], fab[i][idx], _variance(fa[idx], fb[idx])),
                        first_order(fa[idx], fb[idx], fab[j][idx], _variance(fa[idx], fb[idx])),
                    ),
                    resamples,
                )

            results[endpoint] = EndpointIndices(
                endpoint=endpoint, variance=variance, parameters=parameters, pairs=pairs
            )
        return results


def find_no_delivery_effect(run: TraceRun, names: list[str]) -> list[str]:
    """Parameters that cannot move the delivered set on their own.

    Morris found 19 of these: exercised, demonstrably moving scores, and
    yet never changing which records are delivered, because they lift a
    whole class of candidate uniformly and reorder nothing.

    Probed at both ends of each parameter's own range with everything else
    at its shipped value, so this is a statement about acting ALONE. It is
    reported as a marker on the delivery table rather than as grounds for
    dropping a parameter from the design: a parameter that cannot move
    delivery by itself can still move it in combination, and that would
    show up as a nonzero ST with a near-zero S1 -- a finding, not something
    to filter out in advance.
    """
    from .endpoints import delivered_sets

    baseline = delivered_sets(run, ReplayParams())
    ranges = build_ranges(run.param_defaults)

    flat: list[str] = []
    for name in names:
        moved = False
        for unit in (0.0, 1.0):
            params = ReplayParams().with_overrides(
                **{name: ranges[name].to_value(unit)}
            )
            if delivered_sets(run, params) != baseline:
                moved = True
                break
        if not moved:
            flat.append(name)
    return flat


def _variance(fa: np.ndarray, fb: np.ndarray) -> float:
    return float(np.var(np.concatenate([fa, fb]), ddof=1))


def _bootstrap_ci(estimator, resamples: np.ndarray) -> Interval:
    values = np.array([estimator(row) for row in resamples])
    low = float(np.percentile(values, 100 * (1 - CONFIDENCE) / 2))
    high = float(np.percentile(values, 100 * (1 + CONFIDENCE) / 2))
    return Interval(low=low, high=high)


def analyse(
    run: TraceRun,
    names: list[str],
    unexercised: list[str],
    *,
    start_samples: int = 128,
    max_samples: int = 4096,
    st_ci_target: float = 0.02,
    top_k: int = 10,
    seed: int = 20260923,
    probe_delivery: bool = True,
    sampler: str = "auto",
    progress=None,
) -> SobolRun:
    """Double N until the top of the ST ranking is resolved, then stop.

    The stopping rule is about the DECISION the indices feed, not about a
    round number of samples: Phase 2 triage reads the ST ranking, so the
    pass runs until the top-k intervals are narrow enough to order and the
    membership of that top-k has stopped changing between doublings. What
    it costs to get there is a result, not an input.
    """
    analysis = SobolAnalysis(run, names, seed=seed, sampler=sampler)
    convergence: list[dict] = []
    samples = start_samples
    indices = None
    previous_top: list[str] = []

    while True:
        indices = analysis.indices(samples, progress)
        score_top = [p.name for p in indices["score"].ranked()[:top_k]]
        widest = max(
            p.st_ci.half_width for p in indices["score"].ranked()[:top_k]
        )
        stable_membership = set(score_top) == set(previous_top)
        convergence.append(
            {
                "samples": samples,
                "evaluations": analysis.evaluations,
                "widest_st_ci_half_width_top_k": widest,
                "top_k_membership_unchanged": stable_membership,
                "met_target": widest <= st_ci_target and stable_membership,
            }
        )
        if progress:
            progress(analysis.evaluations, note=f"N={samples} widest CI {widest:.4f}")
        if (widest <= st_ci_target and stable_membership) or samples >= max_samples:
            break
        previous_top = score_top
        samples *= 2

    return SobolRun(
        st_ci_target=st_ci_target,
        no_solo_delivery_effect=(
            find_no_delivery_effect(run, names) if probe_delivery else []
        ),
        names=list(names),
        samples=samples,
        evaluations=analysis.evaluations,
        seed=seed,
        sampler=analysis.stream.sampler,
        endpoints=indices,
        unexercised=list(unexercised),
        ranges=analysis.ranges,
        convergence=convergence,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def required_samples(convergence: list[dict], target: float) -> tuple[int, float] | None:
    """Extrapolate the N needed to reach a target interval half-width.

    Fits log(half-width) against log(N) over the doublings actually run and
    solves for the target. Returns (N, fitted exponent) or None when there
    are too few points or the widths did not shrink.

    This is how a run that stopped at its ceiling still answers "what
    sample size is needed" -- with a number extrapolated from the measured
    curve rather than a guess. The exponent is reported alongside because
    it says how trustworthy the extrapolation is: near -0.5 is Monte Carlo
    behaving as theory says, and a much shallower slope means the estimator
    is not yet in its asymptotic regime and the number is optimistic.
    """
    points = [
        (step["samples"], step["widest_st_ci_half_width_top_k"])
        for step in convergence
        if step["widest_st_ci_half_width_top_k"] > 0
    ]
    if len(points) < 2:
        return None

    import math

    xs = [math.log(n) for n, _w in points]
    ys = [math.log(w) for _n, w in points]
    n_points = len(points)
    mean_x = sum(xs) / n_points
    mean_y = sum(ys) / n_points
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    if slope >= 0:
        return None
    intercept = mean_y - slope * mean_x
    needed = math.exp((math.log(target) - intercept) / slope)
    return int(math.ceil(needed)), slope


UNEXERCISED_NOTE = (
    "No activation anywhere in the captured trace, so no perturbation of "
    "them can change any output. They are carried forward as a list, NOT as "
    "zero-influence results: an index of zero that was measured and an index "
    "that could not be measured look identical in a table and mean opposite "
    "things."
)


def format_report(sobol: SobolRun, top: int = 0, pairs: int = 15) -> str:
    lines: list[str] = []
    lines.append("Sobol indices -- retrieval scoring parameters")
    lines.append(
        f"  N={sobol.samples} | k={len(sobol.names)} | "
        f"{sobol.evaluations} model evaluations | sampler {sobol.sampler} | "
        f"seed {sobol.seed}"
    )
    lines.append(
        "  ranges: multipliers swept over [0, 1]; additive terms over "
        "default +/- its own magnitude (same convention as the Morris pass)"
    )
    lines.append(
        f"  intervals are {int(CONFIDENCE * 100)}% bootstrap percentile, "
        f"{BOOTSTRAP_RESAMPLES} resamples"
    )
    if "Sobol" in sobol.sampler:
        lines.append(
            "  the bootstrap prices the sample as iid; a scrambled sequence is "
            "stratified, so the widths are conservative"
        )
    else:
        lines.append(
            "  NOTE: plain Monte Carlo (scipy absent). Roughly an order of "
            "magnitude more samples are needed for the width a scrambled "
            "sequence reaches"
        )

    lines.append("")
    lines.append("  CONVERGENCE (N was doubled until the top-10 ST ranking resolved)")
    lines.append(
        f"    {'N':>6} {'evaluations':>12} {'widest ST CI (top 10)':>23} "
        f"{'top-10 stable':>14}"
    )
    for step in sobol.convergence:
        lines.append(
            f"    {step['samples']:>6} {step['evaluations']:>12} "
            f"{step['widest_st_ci_half_width_top_k']:>23.4f} "
            f"{str(step['top_k_membership_unchanged']):>14}"
        )
    final = sobol.convergence[-1] if sobol.convergence else {}
    if final.get("met_target"):
        lines.append(
            f"    target met at N={sobol.samples} "
            f"({final['widest_st_ci_half_width_top_k']:.4f} half-width)"
        )
    elif final:
        achieved = final["widest_st_ci_half_width_top_k"]
        lines.append(
            f"    TARGET NOT MET: stopped at the N={sobol.samples} ceiling with "
            f"{achieved:.4f} half-width"
        )
        target = sobol.st_ci_target
        estimate = required_samples(sobol.convergence, target) if target else None
        if estimate:
            needed, slope = estimate
            lines.append(
                f"    extrapolated N for a {target:.3f} half-width: ~{needed} "
                f"(fitted exponent {slope:.2f}; -0.50 is textbook Monte Carlo, "
                f"a shallower slope means this is optimistic)"
            )
            lines.append(
                f"    that is ~{needed * (2 * len(sobol.names) + 2):,} model "
                "evaluations"
            )

    for endpoint in ENDPOINTS:
        indices = sobol.endpoints[endpoint]
        lines.append("")
        lines.append(
            f"  ENDPOINT: {endpoint}   (output variance {indices.variance:.6g})"
        )
        lines.append(
            f"    {'rank':>4}  {'parameter':36} {'ST':>8} {'ST 95% CI':>18} "
            f"{'S1':>8} {'ST-S1':>8}  note"
        )
        ranked = indices.ranked()
        if top:
            ranked = ranked[:top]
        solo_flat = set(sobol.no_solo_delivery_effect)
        if endpoint == ENDPOINT_DELIVERY and solo_flat:
            lines.append(
                f"    ({len(solo_flat)} of these cannot move a delivered slot "
                "acting alone, marked solo-flat; a nonzero ST on one of them "
                "is interaction, not a solo effect)"
            )
        for position, parameter in enumerate(ranked, start=1):
            note = ""
            if parameter.additive():
                note = "additive"
            elif parameter.interaction_share > 0.05:
                note = "INTERACTION-DOMINATED" if parameter.s1 < parameter.interaction_share else "interacting"
            if endpoint == ENDPOINT_DELIVERY and parameter.name in solo_flat:
                note = (note + " solo-flat").strip()
            lines.append(
                f"    {position:>4}  {parameter.name:36} {parameter.st:>8.4f} "
                f"[{parameter.st_ci.low:>7.4f},{parameter.st_ci.high:>7.4f}] "
                f"{parameter.s1:>8.4f} {parameter.interaction_share:>8.4f}  {note}"
            )

        lines.append("")
        lines.append(f"    largest S2 pairs on {endpoint}:")
        for pair in indices.top_pairs(pairs):
            interval = (
                f"[{pair.s2_ci.low:>7.4f},{pair.s2_ci.high:>7.4f}]"
                if pair.s2_ci
                else " " * 17
            )
            flag = "significant" if pair.significant() else ""
            lines.append(
                f"      {pair.first:34} x {pair.second:34} "
                f"{pair.s2:>8.4f} {interval}  {flag}"
            )

    lines.append("")
    lines.append(f"  UNEXERCISED ({len(sobol.unexercised)}) -- carried forward, not ranked")
    for line in _wrap(UNEXERCISED_NOTE, 72):
        lines.append(f"  {line}")
    for name in sobol.unexercised:
        lines.append(f"    {name}")

    return "\n".join(lines)


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


def to_dict(sobol: SobolRun) -> dict:
    """Serializable results: parameter names and numbers only, no vault data."""
    return {
        "samples": sobol.samples,
        "evaluations": sobol.evaluations,
        "seed": sobol.seed,
        "sampler": sobol.sampler,
        "confidence": CONFIDENCE,
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "convergence": sobol.convergence,
        "st_ci_target": sobol.st_ci_target,
        "required_samples_for_target": (
            required_samples(sobol.convergence, sobol.st_ci_target)
            if sobol.st_ci_target
            else None
        ),
        "unexercised": list(sobol.unexercised),
        "unexercised_note": UNEXERCISED_NOTE,
        "no_solo_delivery_effect": list(sobol.no_solo_delivery_effect),
        "ranges": {
            name: {"family": r.family, "low": r.low, "high": r.high, "default": r.default}
            for name, r in sobol.ranges.items()
            if name in set(sobol.names)
        },
        "endpoints": {
            endpoint: {
                "variance": indices.variance,
                "parameters": [
                    {
                        "name": p.name,
                        "s1": p.s1,
                        "s1_ci": [p.s1_ci.low, p.s1_ci.high],
                        "st": p.st,
                        "st_ci": [p.st_ci.low, p.st_ci.high],
                        "interaction_share": p.interaction_share,
                    }
                    for p in indices.ranked()
                ],
                "pairs": [
                    {
                        "first": pair.first,
                        "second": pair.second,
                        "s2": pair.s2,
                        "s2_ci": [pair.s2_ci.low, pair.s2_ci.high] if pair.s2_ci else None,
                    }
                    for pair in sorted(indices.pairs, key=lambda p: -abs(p.s2))
                ],
            }
            for endpoint, indices in sobol.endpoints.items()
        },
    }


def load_results(results: dict, st_ci_target: float = 0.0) -> SobolRun:
    """Rebuild a SobolRun from saved results so it can be re-rendered.

    A Sobol pass costs hours of evaluations and the numbers outlive the
    process that produced them. Without this, re-reading a finished run
    with a changed report -- a new caveat, a different top-N -- would mean
    paying for the evaluations again, which is how stale artifacts get
    kept around instead of regenerated.

    st_ci_target is supplied by the caller because it is a property of the
    question being asked of the numbers, not of the numbers themselves:
    the same run answers "is this resolved to 0.02" and "to 0.05".
    """
    endpoints: dict[str, EndpointIndices] = {}
    for endpoint, payload in results["endpoints"].items():
        parameters = [
            ParameterIndices(
                name=entry["name"],
                s1=entry["s1"],
                st=entry["st"],
                s1_ci=Interval(*entry["s1_ci"]),
                st_ci=Interval(*entry["st_ci"]),
            )
            for entry in payload["parameters"]
        ]
        pairs = [
            PairIndex(
                first=entry["first"],
                second=entry["second"],
                s2=entry["s2"],
                s2_ci=Interval(*entry["s2_ci"]) if entry.get("s2_ci") else None,
            )
            for entry in payload["pairs"]
        ]
        endpoints[endpoint] = EndpointIndices(
            endpoint=endpoint,
            variance=payload["variance"],
            parameters=parameters,
            pairs=pairs,
        )

    ranges = {
        name: ParameterRange(
            name=name,
            family=entry["family"],
            low=entry["low"],
            high=entry["high"],
            default=entry["default"],
        )
        for name, entry in results.get("ranges", {}).items()
    }

    return SobolRun(
        names=[p["name"] for p in results["endpoints"]["score"]["parameters"]],
        samples=results["samples"],
        evaluations=results["evaluations"],
        seed=results["seed"],
        sampler=results["sampler"],
        endpoints=endpoints,
        unexercised=results.get("unexercised", []),
        ranges=ranges,
        convergence=results.get("convergence", []),
        st_ci_target=results.get("st_ci_target") or st_ci_target,
        no_solo_delivery_effect=results.get("no_solo_delivery_effect", []),
    )


def pairs_involving(results: dict, endpoint: str, name: str, limit: int = 10) -> list[dict]:
    """The largest S2 entries involving one parameter, read from saved results.

    Operates on the serialized dict rather than a live SobolRun so a
    question can be asked of a run that finished hours ago without paying
    for it again.

    This is the query that separates "counted twice" from "interacting",
    which are different claims and are easy to conflate. Two additive
    constants entering the same linear composition CANNOT interact with
    each other -- the cross partial derivative is identically zero -- no
    matter how thoroughly the pipeline double counts them. What double
    counting at two different points in the composition produces instead
    is an asymmetry in what each one interacts WITH: a term inside the tier
    multiply interacts with tier, and the same term added after it does
    not. So the hypothesis is tested by looking at each parameter's
    partners, not at the pair between them.
    """
    entries = results["endpoints"][endpoint]["pairs"]
    mine = [
        entry
        for entry in entries
        if name in (entry["first"], entry["second"])
    ]
    mine.sort(key=lambda entry: -abs(entry["s2"]))
    return mine[:limit]


def check_pair(
    sobol: SobolRun, endpoint: str, first: str, second: str
) -> tuple[float, bool, str]:
    """Look up one hypothesised pair and say whether the data supports it."""
    indices = sobol.endpoints[endpoint]
    pair = indices.pair(first, second)
    if pair is None:
        return 0.0, False, "pair not in the analysed set"
    if pair.s2_ci is None:
        return pair.s2, False, "point estimate only, no interval computed"
    if pair.s2_ci.low > 0:
        return pair.s2, True, "interval excludes zero"
    if pair.s2_ci.high < 0:
        return pair.s2, True, "interval excludes zero (negative)"
    return pair.s2, False, "interval contains zero"
