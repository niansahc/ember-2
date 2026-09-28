"""
tools/retrieval_trace/ranges.py

The perturbation range for each parameter, derived from that parameter's
own scale.

This is the most consequential judgement call in the whole screening, and
it is easy to miss that it is a judgement call at all. Morris works in a
normalized unit cube: an elementary effect is the change in the output for
a full-width step in one normalized coordinate. So mu* is not "how much
does this parameter matter" in the abstract -- it is "how much does the
output move when this parameter is swept across THIS range". Choose the
ranges and you have chosen the ranking.

A single uniform range across the whole vector would be the worst option
available. `auth.mixed` is a multiplier on [0, 1] and `ret.lexical.entity_hit`
is an additive term near 0.20; sweeping both over, say, +/-0.5 would sweep
the multiplier through meaningless territory (a negative weight inverts every
score it touches) while barely moving the additive term off its shipped
value. The families are given different treatment because they are different
kinds of number.

Three families:

BOUNDED -- the prior terms and the tier weights. ADR-044 asserts a contract
    on these: `prior x tier` stays inside [0.8722, 1.1278], split evenly so
    `prior` lives in [0.9339, 1.1278] and `tier` in [0.9339, 1.0]. The range
    is that contract interval and nothing wider.

    This family is new, and it exists because [0, 1] is wrong for these two
    ways at once. It cannot even EXPRESS their shipped values --
    prior.kind.experience is 1.0581 -- so screening it there would exclude
    the value the system actually runs. And sweeping tier.cold to 0.0 probes
    a system the contract forbids, which is how the old unbounded stack got
    its authority in the first place: a term screened over territory it may
    not occupy can top the sensitivity table for movement it can never make.

    The cost is stated rather than hidden: mu* and ST computed over these
    intervals are NOT comparable with the pre-ADR-044 runs, which screened
    the same mechanisms over [0, 1] or over +/-100%. That break is deliberate.
    The alternative is comparability with measurements of a pipeline that no
    longer exists.

MULTIPLIER -- the authorship weights. Each is a fraction of a score by
    construction and is NOT bounded by the ADR-044 contract (the authorship
    multiplier is a gate, and third_party is legitimately 0.0), so the full
    space of a fraction, [0, 1], is right for them. Screening them over
    different intervals from each other would make their mu* incomparable.

ADDITIVE -- the remaining score constants: the lexical and intent terms, the
    policy preference terms, and the project boost. The range is the shipped
    value plus or minus its own magnitude, so each is swept from zero-ish to
    twice-shipped: a +/-100% retune, the size of change someone would
    actually consider. Terms at or near zero would be pinned by a purely
    proportional rule, so the half-width is floored at ADDITIVE_FLOOR.

The floor is the one arbitrary number here, and it is derived rather than
chosen: a third of the measured cosine spread, so that a constant currently
worth nothing still gets screened over a range that could plausibly matter
against the signal it competes with, without handing it a range so wide it
tops the table by construction. It is computed from prior.COSINE_SPREAD
rather than written out, because the figure it was originally derived from
(0.1504, the fixture corpus) has since been superseded by the production
measurement of 0.0815 -- and a derivation stated in a comment while the
number stays put is the failure mode this whole module is about.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.context import prior as _prior

# Half-width floor for additive parameters whose shipped value is at or near
# zero: a third of the measured production top-8 cosine spread (0.0815), so
# 0.0272. Was 0.05, a third of the withdrawn fixture figure.
ADDITIVE_FLOOR = _prior.COSINE_SPREAD / 3.0

FAMILY_BOUNDED = "bounded"
FAMILY_MULTIPLIER = "multiplier"
FAMILY_ADDITIVE = "additive"

# The ADR-044 contract interval applies to these.
_BOUNDED_PREFIXES = ("prior.", "tier.")

# Fractions of a score, unbounded by the contract.
#
# An exact-name escape hatch stood beside this for refl.base_discount and
# refl.recency_scale, both retired by ADR-044. It is gone rather than left as
# an empty frozenset: an unused hook reads as a supported extension point.
_MULTIPLIER_PREFIXES = ("auth.",)


@dataclass(frozen=True)
class ParameterRange:
    name: str
    family: str
    low: float
    high: float
    default: float

    @property
    def width(self) -> float:
        return self.high - self.low

    def to_value(self, unit: float) -> float:
        """Map a normalized coordinate in [0, 1] to a parameter value."""
        return self.low + unit * self.width

    def to_unit(self, value: float) -> float:
        if self.width == 0:
            return 0.0
        return (value - self.low) / self.width


def family_of(name: str) -> str:
    if name.startswith(_BOUNDED_PREFIXES):
        return FAMILY_BOUNDED
    if name.startswith(_MULTIPLIER_PREFIXES):
        return FAMILY_MULTIPLIER
    return FAMILY_ADDITIVE


def range_for(name: str, default: float) -> ParameterRange:
    family = family_of(name)
    if family == FAMILY_BOUNDED:
        # tier's ceiling is 1.0, not PRIOR_MAX: the contract splits the budget
        # so that tier only ever discounts and the prior carries the whole
        # upward half. Screening tier above 1.0 would let a cold record be
        # PROMOTED for being cold, which is not a retune of the contract but a
        # different contract.
        low = _prior.TIER_MIN if name.startswith("tier.") else _prior.PRIOR_MIN
        high = 1.0 if name.startswith("tier.") else _prior.PRIOR_MAX
        return ParameterRange(name=name, family=family, low=low, high=high,
                              default=default)
    if family == FAMILY_MULTIPLIER:
        return ParameterRange(name=name, family=family, low=0.0, high=1.0, default=default)
    half_width = max(abs(default), ADDITIVE_FLOOR)
    return ParameterRange(
        name=name,
        family=family,
        low=default - half_width,
        high=default + half_width,
        default=default,
    )


def build_ranges(defaults: dict[str, float]) -> dict[str, ParameterRange]:
    return {name: range_for(name, value) for name, value in defaults.items()}
