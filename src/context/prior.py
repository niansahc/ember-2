"""
src/context/prior.py

ADR-044: the metadata prior, assembled once.

Before this module, type, role, content_kind, length and recency were
applied additively at two separate stages -- once in semantic_search and
again in ContextRanker._score_memory_item -- with no normalization and no
commensurability rule. Type and role were counted twice, recency three
times. The composed query-independent swing reached 0.98 against a
production top-8 cosine spread of 0.0815, roughly 12:1: the metadata was
the ranking signal and cosine was the tiebreaker.

The contract is `score = cosine x prior x tier`, with the prior a bounded
multiplier in cosine units so the three downstream consumers that
threshold the composed score keep their meaning.

THE BOUND
---------
`prior x tier` is bounded to [0.8722, 1.1278]. That is 1 +/- B where

    B = 0.0815 / 0.6375 = 0.12784

is the measured production top-8 raw cosine spread over the mean rank-1
cosine (#236). The principle is ADR-044's: a multiplier permitted to move
a record further than the entire observable similarity range is not a
tiebreaker, it is the ranking signal.

The budget is split evenly in the multiplicative sense, sqrt(0.8722) each,
so that the worst case of both together still lands on the bound:

    tier  in [0.9339, 1.0]
    prior in [0.9339, 1.1278]

MAGNITUDES
----------
None of the previous constants survive on their own authority. Their
stated provenance -- an eval with "15 benchmark cases" -- has 5 cases and
no graded relevance, so there is nothing to carry forward. Each retained
term is instead derived from its Sobol total-order index on the DELIVERY
endpoint (#232), which measures whether the term changes what the model
actually receives rather than whether it moves a number:

    deviation_i = (1 - PRIOR_MIN) * (ST_i / ST_max)

with ST_max the largest ST among retained prior terms (rank.len.lt50,
0.1930). Direction is preserved from the previous constant's sign. So the
term the measurement says matters most is allowed to consume the prior's
entire half of the bound on its own, and everything else is scaled below
it in proportion to how much it moves delivery.

Terms whose ST says they never reorder delivery are set to 1.0 and gone.
That is the smallest value consistent with the contract, which is the
rule for a constant with no defensible basis. The type ladder is the
whole of that group: rank.type.conversation, rank.type.reflection and
rank.type.other all appear in Sobol's no_solo_delivery_effect list and in
Morris's no-effect class on delivery. They were also the terms counted
twice.

ROLE is not here. ADR-044 amendment 4a moves it out of the scoring budget
to a hard predicate on the authorship column, measured in PR #217: the
role pile is responsible for exactly one incident and a predicate covers
it completely, at no cost against a bounded budget. See
src/context/role_predicate.py.
"""

from __future__ import annotations

# Measured production top-8 raw cosine spread and mean rank-1 cosine (#236,
# tools/cosine_spread.py). The bound is asserted against these, not against
# the 0.1504 fixture figure ADR-044 originally carried.
COSINE_SPREAD = 0.0815
COSINE_MEAN_RANK1 = 0.6375
RELATIVE_SPREAD = COSINE_SPREAD / COSINE_MEAN_RANK1  # 0.12784

# The composed bound, and the even split between its two factors.
COMPOSED_MIN = 1.0 - RELATIVE_SPREAD   # 0.87216
COMPOSED_MAX = 1.0 + RELATIVE_SPREAD   # 1.12784
_FACTOR_MIN = COMPOSED_MIN ** 0.5      # 0.93390

PRIOR_MIN = _FACTOR_MIN
PRIOR_MAX = COMPOSED_MAX
TIER_MIN = _FACTOR_MIN

# Sobol ST on the delivery endpoint (#232), for the terms that keep a
# magnitude. The largest sets the scale.
_ST = {
    "len_under_50": 0.1930,
    "recency_d365": 0.1713,
    "kind_experience": 0.1696,
    "recency_older": 0.1559,
    "kind_user_content": 0.0724,
    "len_over_1200": 0.0451,
}
_ST_MAX = max(_ST.values())


# The prior's own share of the bound, which is what a single term is
# scaled against. Scaling to the full composed B instead would let one
# term saturate the clamp on its own and make every other term
# unobservable behind it.
_PRIOR_BUDGET = 1.0 - PRIOR_MIN  # 0.06610


def _deviation(term: str) -> float:
    """How far this term may move a record, as a fraction of 1.0."""
    return _PRIOR_BUDGET * (_ST[term] / _ST_MAX)


# Derived multipliers. Sign follows the previous constant's direction; only
# the magnitude is re-derived.
LEN_UNDER_50 = 1.0 - _deviation("len_under_50")        # 0.9339
LEN_OVER_1200 = 1.0 - _deviation("len_over_1200")      # 0.9845
KIND_EXPERIENCE = 1.0 + _deviation("kind_experience")  # 1.0581
KIND_USER_CONTENT = 1.0 + _deviation("kind_user_content")  # 1.0248
KIND_QUESTION = 1.0 - _deviation("kind_user_content")  # 0.9752, mirrored
KIND_ANSWER = 1.0 - _deviation("kind_user_content")    # 0.9752, mirrored

# Recency. Five buckets, scaled so the widest-ST bucket carries the full
# recency deviation and the ladder keeps its previous relative ordering
# (+0.18 / +0.12 / +0.06 / +0.02 / -0.03 becomes a multiplicative ladder of
# the same ordering). d365 and older are the two the Sobol run resolved;
# the fresher buckets were never taken on this corpus (#235) so they are
# scaled by the same factor rather than measured independently.
_RECENCY_SCALE = _deviation("recency_d365") / 0.18
RECENCY = {
    "d7": 1.0 + 0.18 * _RECENCY_SCALE,
    "d30": 1.0 + 0.12 * _RECENCY_SCALE,
    "d90": 1.0 + 0.06 * _RECENCY_SCALE,
    "d365": 1.0 + 0.02 * _RECENCY_SCALE,
    "older": 1.0 - 0.03 * _RECENCY_SCALE,
    "unparsed": 1.0,
}

# Reflections are derived artifacts; the source they summarise is usually
# more specific. Retained as the smallest expressible discount rather than
# the previous 0.95, because refl.base_discount is in Sobol's
# no_solo_delivery_effect list -- it never reorders delivery, so the rule
# for an undefensible constant applies.
REFLECTION_DISCOUNT = 1.0


def clamp(value: float) -> float:
    """Hold the prior inside its half of the bound."""
    return max(PRIOR_MIN, min(PRIOR_MAX, value))


def assemble(
    *,
    content_kind: str | None,
    content_length: int,
    recency_bucket: str,
    is_reflection: bool = False,
) -> float:
    """The metadata prior for one candidate, as a bounded multiplier.

    Query-independent by definition. Anything that depends on the query --
    lexical overlap, entity match, intent conditioning -- adjusts the
    similarity estimate and stays at the retrieval stage; it is not part
    of the prior and is not bounded by it.
    """
    prior = 1.0

    if content_kind == "experience":
        prior *= KIND_EXPERIENCE
    elif content_kind == "user_content":
        prior *= KIND_USER_CONTENT
    elif content_kind == "question":
        prior *= KIND_QUESTION
    elif content_kind == "answer":
        prior *= KIND_ANSWER

    if content_length < 50:
        prior *= LEN_UNDER_50
    elif content_length > 1200:
        prior *= LEN_OVER_1200

    prior *= RECENCY.get(recency_bucket, 1.0)

    if is_reflection:
        prior *= REFLECTION_DISCOUNT

    return clamp(prior)
