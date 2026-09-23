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

A single uniform range across all 79 would be the worst option available.
`tier.cold` is a multiplier on [0, 1] and `rank.role.assistant` is an
additive penalty near -0.25; sweeping both over, say, +/-0.5 would sweep
the multiplier through meaningless territory (a negative tier weight
inverts every score it touches) while barely moving the additive term off
its shipped value. The two families are given different treatment because
they are different kinds of number.

Two families:

MULTIPLIER -- tier weights, decay weights, authorship weights, and the
    reflection base discount. Each is a fraction of a score by
    construction, and the full space of a fraction is [0, 1]. The range is
    that whole space, not a neighbourhood of the shipped value: cold at 0.3
    and hot at 1.0 are points on one scale, and screening them over
    different intervals would make their mu* incomparable.

ADDITIVE -- every score constant. The range is the shipped value plus or
    minus its own magnitude, so each additive term is swept from zero-ish
    to twice-shipped: a +/-100% retune, the size of change someone would
    actually consider. Terms at or near zero would be pinned by a purely
    proportional rule, so the half-width is floored at ADDITIVE_FLOOR.

The floor is the one arbitrary number here. It is set at 0.05 -- a third of
the measured cosine spread on this corpus (0.1504, ADR-044) -- so that a
constant currently worth nothing still gets screened over a range that
could plausibly matter against the signal it competes with, without
handing it a range so wide that it tops the table by construction.
"""

from __future__ import annotations

from dataclasses import dataclass

# Half-width floor for additive parameters whose shipped value is at or
# near zero. See the module docstring for where 0.05 comes from.
ADDITIVE_FLOOR = 0.05

FAMILY_MULTIPLIER = "multiplier"
FAMILY_ADDITIVE = "additive"

# Prefixes and exact names whose parameters are multiplicative weights.
_MULTIPLIER_PREFIXES = ("tier.", "auth.", "decay.")
_MULTIPLIER_NAMES = frozenset({"refl.base_discount", "refl.recency_scale"})


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
    if name in _MULTIPLIER_NAMES or name.startswith(_MULTIPLIER_PREFIXES):
        return FAMILY_MULTIPLIER
    return FAMILY_ADDITIVE


def range_for(name: str, default: float) -> ParameterRange:
    family = family_of(name)
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
