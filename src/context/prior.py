"""
src/context/prior.py

ADR-044: the metadata prior, assembled once, bounded by construction.

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

Asymmetric at the top on purpose. Tier's ceiling is 1.0: a record is
spared the cold discount, never promoted for being hot. The prior carries
the whole upward half.

WHAT THE PRIOR NOW COVERS
-------------------------
Five families, and every one of them is a multiplier inside the bound.
Three were already here. Two were additive terms outside the bound until
they were brought in, because ADR-044 claimed composition was bounded
while they sat outside it:

    kind          content_kind
    length        content length
    recency       age bucket
    policy        the per-policy preference terms -- prefer_experiences,
                  prefer_active_work, prefer_exact_matches. Were +0.20,
                  +0.22 and -0.05/+0.03 additive, the largest 2.7x the
                  entire measured cosine spread.
    project       ADR-007's project match. Was +0.15 additive, applied
                  AFTER the tier multiply, so tier could not attenuate it.

Bringing project in also removes the last ordering defect ADR-044
decision 1 names. It was the final additive term landing after tier, and
multiplication commutes, so there is no longer an order to get wrong.

The policy terms are one FAMILY rather than three independent terms,
because no policy sets more than one of the three flags -- verified across
all ten policies in src/context/policies.py. At most one can fire per
query, so the worst case is one arm, not three.

Two multipliers in the same composition are deliberately NOT here, and
ADR-044's 2026-09-30 amendment states why for each: the authorship gate
(a suppression mechanism, not a tiebreaker, governed by the
incident-reproduction suite) and the per-policy weight split (made
uniform per channel, and a uniform positive scale on a list cannot
reorder that list).

THE ALLOCATION: LOG SPACE, NOT PER-TERM
---------------------------------------
Magnitudes come from Sobol total-order indices on the DELIVERY endpoint,
which measures whether a term changes what the model receives rather than
whether it moves a number.

The previous allocation was

    deviation_i = (1 - PRIOR_MIN) * (ST_i / ST_max)

and it was wrong in a way that showed up in production. It hands each term
independently the right to consume the prior's entire budget -- the
largest-ST term took exactly that, so LEN_UNDER_50 == PRIOR_MIN by
construction -- while `assemble` composes terms by MULTIPLYING them. Any
record taking two downward terms therefore left the bound and was clamped
back to it. Measured on the shipped constants: 9 of 90 branch
combinations clamped, all of them short records, 30% of the
short-record space, and inside that region the prior was a CONSTANT with
kind and recency erased.

The budget is allocated per term and spent multiplicatively. So allocate
in log space instead:

    log_dev_i = log(BOUND) * ST_i / S

where S sums the ST of the worst one-term-per-family case in that
direction. The families are mutually exclusive, so a record takes at most
one arm from each, and the worst case is a sum over families rather than
over terms. The product of the worst case is then exactly the bound, and
the clamp becomes unreachable -- it stays as an assertion, not as the
enforcement.

Each direction is allocated separately, because the budget is asymmetric:
the downward half is log(0.9339) and the upward half log(1.1278).

WHERE THE NUMBERS COME FROM
---------------------------
One Sobol run against the production corpus, 2026-10-01, on the CORRECTED
delivery endpoint (#227).

    N=4096, k=37, 212,992 evaluations
    scipy.qmc.Sobol(scrambled), seed 20260923
    1000 bootstrap resamples, 95% intervals
    st_ci_target 0.020: MET -- delivery 0.0147, score 0.0104
    the extrapolation independently demanded N=2098

This supersedes the 2026-09-30 values, which were taken on an endpoint that
measured the context PACKET rather than what the prompt renders. The packet
carries 4 to 6 non-profile memory records and the prompt renders 4
(src/context/render_window.py), so that endpoint was blind to the one
boundary that decides whether a record reaches the model at all: a candidate
moving between rank 4 and rank 5 leaves packet membership unchanged, and the
measured distance for the most consequential reordering there is was zero.

The two runs were compared directly -- same trace, same 37-parameter vector,
same seed, same sampler, the render window the only difference. 17 of 25
swept terms moved beyond the larger of their two half-widths, so the earlier
magnitudes are not approximately right and carrying them would repeat the
#232 error this file already records. Score ST moved for 0 of 25, as it must:
the score endpoint never saw a delivered set.

Three terms show the correction working in both directions, and they are the
clearest evidence the old endpoint measured the wrong quantity:

    pol_exact_question  ST 0.0000 -> 0.0525   freed: its only effect is at
    proj_match          ST 0.0000 -> 0.0080   the 4/5 boundary
    reflection          ST 0.1482 -> 0.0208   newly solo-flat: its effect
                                              lived at packet positions 2-3,
                                              which are never rendered

The corrected endpoint also converges FASTER, not slower: it met the target
at N=4096 where the packet definition needed N=8192, because delivery
variance is 4.4x higher on the rendered set. The packet definition was
averaging a quantity that barely moved.

A term whose ST says it cannot move delivery takes 1.0 and is gone. On this
run that is KIND_ANSWER, and its justification is now repaired rather than
inherited: ST 0.0000 and solo-flat under BOTH endpoint definitions, plus a
direct probe -- sweeping it to either end of its range moves zero delivered
refs, packet and rendered. Its retirement was not an artefact of the old
endpoint.

The unparsed recency bucket is 1.0 for a STRUCTURAL reason, not a measured
one. A previous version of this docstring listed it here as ST-derived; it
has no entry in _ST and never had one, and it was never measured. See
recency_bucket_or_unparsed for what it actually is.

WHAT THE ALLOCATION RESTS ON: ST, NOT S1
----------------------------------------
Seven of the fourteen measured terms are interaction-dominated -- ST-S1
exceeds S1, so most of their total effect appears only in combination with
other terms:

    recency_older     ST 0.2195  S1 0.0535      kind_user_content ST 0.1191  S1 0.0289
    recency_d365      ST 0.2371  S1 0.1030      pol_prefer_experience ST 0.0323  S1 0.0041
    len_under_50      ST 0.0559  S1 0.0207      pol_exact_other   ST 0.0524  S1 0.0229
    reflection        ST 0.0208  S1 -0.0007

Allocating on ST is deliberate and it is not a caveat. The quantity this
bound constrains is the product of one arm per family firing SIMULTANEOUSLY
-- that is what the worst case below is, and what the clamp's unreachability
is asserted against. Simultaneous is precisely the regime interactions
describe, so the total-effect index is the one that matches the claim. S1
would be the right statistic if the bound constrained each term acting
alone, and it does not; allocating on S1 would under-fund exactly the terms
that do their work in combination and the worst-case product would land
inside the bound, wasting budget the contract permits.

This is the opposite case to the recency ladder below, where ST is the WRONG
statistic -- and the distinction is worth holding onto. Ordering is a
pairwise question about which arm should rank higher, and a variance share
cannot answer it. Magnitude under simultaneous firing is a joint-variance
question, and a variance share is exactly what answers it.

Two terms sit at the noise floor and are flagged rather than hidden:
reflection's S1 is NEGATIVE (-0.0007), and len_over_1200's S1 (0.0127)
exceeds its ST (0.0058), which is impossible in theory. Both are estimator
noise at small magnitudes. Their derived factors are within 0.3% of 1.0, so
nothing downstream turns on them, but neither is a measurement anyone should
lean on.

THE RECENCY LADDER IS A MIXED DERIVATION
----------------------------------------
Stated plainly because a later reader must not treat it as uniformly
measured.

    MEASURED (contribute the family's magnitude):
        d365  ST 0.2371    older  ST 0.2195
    NO SIGNAL (in the vector, swept, measured exactly zero):
        d30   ST 0.0000, under both endpoint definitions
    CARRIED (ordering only, never measured):
        d7    d90   unexercised

So FOUR OF SIX ARMS CARRY NO SIGNAL on this corpus, and the family's
magnitude rests on TWO. That is the honest statement and it is weaker than
"mixed derivation" suggests: this is a carried ladder with two measured
anchors, not a measured family. d30 reading exactly 0.0000 is new -- the
previous derivation had it at 0.0111 on the packet endpoint, and on the
rendered endpoint it cannot move a delivered slot at all.

d7 and d90 are UNEXERCISED on the production corpus -- nothing retrieved
falls within 7 days or in the 31-90 day band (see issue #252, where that
is a symptom of a larger problem). An unexercised parameter has no
measurement at any sample count; it is a corpus fact, not a convergence
one. d30 is different and the difference matters: it IS exercised, and it
measured zero. Two kinds of zero, and the run reports them separately.

The ladder's ORDERING is therefore carried from the additive ladder these
replaced (+0.18 / +0.12 / +0.06 / +0.02 / -0.03) and its MAGNITUDE comes
from the measured arms, scaled as one family. That is what the previous
version of this file already did, and here it is load-bearing rather than
incidental, for a reason worth spelling out:

Per-arm ST allocation would invert the ladder. Measured d365 (0.2371)
against d30's measured zero would give a year-old record a boost and a
month-old one nothing at all. That is not a
finding, it is a broken prior: recency arms are mutually exclusive, so
they are compared ACROSS records, and inverting them inverts the
preference. ST is an activation-weighted variance share -- it measures how
much a term moves delivery on this corpus, not which arm of an ordinal
ladder should rank higher. Using it to ORDER an ordered ladder is a
category error, and the ladder is the one place in this file where
measurement cannot set the ordering.

ROLE is not here. ADR-044 amendment 4a moves it out of the scoring budget
to a hard predicate on the authorship column, measured in PR #217: the
role pile is responsible for exactly one incident and a predicate covers
it completely, at no cost against a bounded budget. See
src/context/role_predicate.py.
"""

from __future__ import annotations

import math

from src.observability.guard_counters import branch, count

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

# ---------------------------------------------------------------------------
# Measured Sobol ST on the delivery endpoint. See "where the numbers come
# from" above for the run that produced these and its convergence state.
# Half-widths are carried alongside so a reader can see which terms are
# tightly resolved without opening the run artefact.
# ---------------------------------------------------------------------------

# ST, its 95% half-width, and S1 -- S1 because seven of these are
# interaction-dominated and a reader has to be able to see that without
# opening the run artefact. See "what the allocation rests on" above.
_ST = {
    "kind_experience": 0.2216,      # +/- 0.0131   S1 0.1090
    "kind_question": 0.1687,        # +/- 0.0101   S1 0.1215
    "kind_user_content": 0.1191,    # +/- 0.0072   S1 0.0289  interaction-dominated
    "kind_answer": 0.0000,          # solo-flat under BOTH endpoints -> identity
    "recency_d365": 0.2371,         # +/- 0.0147   S1 0.1030  interaction-dominated
    "recency_older": 0.2195,        # +/- 0.0142   S1 0.0535  interaction-dominated
    "recency_d30": 0.0000,          # measured zero, not unexercised
    "len_under_50": 0.0559,         # +/- 0.0038   S1 0.0207  interaction-dominated
    "len_over_1200": 0.0058,        # +/- 0.0007   S1 0.0127  noise floor: S1 > ST
    "pol_prefer_active_work": 0.2453,   # +/- 0.0135   S1 0.2030
    "pol_prefer_experience": 0.0323,    # +/- 0.0022   S1 0.0041  interaction-dominated
    "pol_exact_question": 0.0525,       # +/- 0.0033   S1 0.0306
    "pol_exact_other": 0.0524,          # +/- 0.0035   S1 0.0229  interaction-dominated
    "proj_match": 0.0080,           # +/- 0.0008   S1 0.0054
    "reflection": 0.0208,           # +/- 0.0022   S1 -0.0007  noise floor: S1 < 0
}

# The recency family is scaled as a whole -- see "the recency ladder is a
# mixed derivation". This is the family's measured magnitude: its strongest
# measured signal. The RULE is unchanged from the previous derivation; the
# inputs moved, and d365 now outranks older where older led before. Only two
# arms are measured at all, so this is the stronger of two rather than the
# strongest of five.
#
# And the two are not distinguishable at this N: 0.2371 +/- 0.0147 against
# 0.2195 +/- 0.0142, a gap of 0.0176 the intervals nearly cover. So which arm
# "wins" is not a finding, and the allocation is close to indifferent -- taking
# older instead moves the derived factors in the third decimal. Recorded because
# this family sets 44% of the downward budget and the whole upward ladder, so a
# reader is entitled to know the pick is a coin flip between tied arms.
_RECENCY_FAMILY_ST = _ST["recency_d365"]

# The additive ladder whose ORDERING is carried. Only the ratios survive; the
# magnitude comes from _RECENCY_FAMILY_ST.
_RECENCY_LADDER = {
    "d7": 0.18,
    "d30": 0.12,
    "d90": 0.06,
    "d365": 0.02,
    "older": -0.03,
}

# The worst one-arm-per-family case in each direction. Mutually exclusive
# families, so this is a sum over FAMILIES, not over terms -- which is the
# whole reason the allocation fits.
_WORST_DOWN = {
    "kind": _ST["kind_question"],
    "length": _ST["len_under_50"],
    "recency": _RECENCY_FAMILY_ST,
    "policy": _ST["pol_exact_question"],
    "reflection": _ST["reflection"],
}
_WORST_UP = {
    "kind": _ST["kind_experience"],
    "recency": _RECENCY_FAMILY_ST,
    "policy": _ST["pol_prefer_active_work"],
    "project": _ST["proj_match"],
    # No upward length arm: both length branches are discounts.
}

_S_DOWN = sum(_WORST_DOWN.values())
_S_UP = sum(_WORST_UP.values())
_LOG_DOWN = math.log(PRIOR_MIN)   # negative
_LOG_UP = math.log(PRIOR_MAX)     # positive


def _family_share(family: str, downward: bool) -> float:
    """This family's log-space share of its direction's budget."""
    if downward:
        return _LOG_DOWN * _WORST_DOWN[family] / _S_DOWN
    return _LOG_UP * _WORST_UP[family] / _S_UP


def _factor(family: str, term: str, *, downward: bool) -> float:
    """One term's multiplier: its family's share, scaled by its own ST.

    The family's worst arm takes the whole share; a lesser arm in the same
    family and direction takes it in proportion to its own ST. So a family
    can never exceed its allocation no matter which arm fires.

    No zero-ST guard: every worst-arm entry is a non-zero measured ST, and
    neither term whose ST is zero reaches here. kind_answer is written as the
    literal identity below, which is where a "this term cannot move delivery"
    decision belongs; recency_d30 is an arm of the carried ladder, whose factors
    come from _RECENCY_LADDER ratios rather than from per-arm ST.
    """
    worst = (_WORST_DOWN if downward else _WORST_UP)[family]
    return math.exp(_family_share(family, downward) * _ST[term] / worst)


# ---------------------------------------------------------------------------
# Derived multipliers. Direction follows the previous constant's sign; only
# the magnitude is derived.
# ---------------------------------------------------------------------------

KIND_EXPERIENCE = _factor("kind", "kind_experience", downward=False)        # 1.0678
KIND_USER_CONTENT = _factor("kind", "kind_user_content", downward=False)    # 1.0309
KIND_QUESTION = _factor("kind", "kind_question", downward=True)             # 0.9679
# ST 0.0000 and in no_solo_delivery_effect: it cannot move delivery, so it
# takes the smallest value consistent with the contract. Previously this
# mirrored KIND_QUESTION; the measurement separates them.
KIND_ANSWER = 1.0

LEN_UNDER_50 = _factor("length", "len_under_50", downward=True)             # 0.9876
LEN_OVER_1200 = _factor("length", "len_over_1200", downward=True)           # 0.9995

POL_PREFER_ACTIVE_WORK = _factor("policy", "pol_prefer_active_work", downward=False)
POL_PREFER_EXPERIENCE = _factor("policy", "pol_prefer_experience", downward=False)
POL_EXACT_OTHER = _factor("policy", "pol_exact_other", downward=False)
POL_EXACT_QUESTION = _factor("policy", "pol_exact_question", downward=True)

PROJECT_MATCH = _factor("project", "proj_match", downward=False)            # 1.0063

REFLECTION_DISCOUNT = _factor("reflection", "reflection", downward=True)    # 0.9995

# Recency. The ladder has exactly one downward arm, so it takes the family's
# whole downward share directly and the upward arms scale against d7. Written
# without a signed branch because a per-direction scale constant would exist
# only to be multiplied back by the single ratio that defined it.
_RECENCY_SCALE_UP = _family_share("recency", False) / _RECENCY_LADDER["d7"]

RECENCY = {
    bucket: math.exp(_RECENCY_SCALE_UP * ratio)
    for bucket, ratio in _RECENCY_LADDER.items()
    if ratio > 0
}
RECENCY["older"] = math.exp(_family_share("recency", True))
RECENCY["unparsed"] = 1.0

# ---------------------------------------------------------------------------
# Term tables, so assemble() reads its factor and its counter arm from one
# place. The "none" entries are the identity and exist so every candidate
# takes a named branch -- an unnamed fall-through is a hole in the traffic
# window, which is how ranker.tier's arms came to be under-declared.
#
# Two consumers besides assemble(): tools/guard_counter_sites.py reads the
# arm domains off these tables so the counter inventory follows the code, and
# tools/retrieval_trace/params.py imports the values so the sensitivity
# vector does not copy them.
# ---------------------------------------------------------------------------

_KIND_FACTORS = {
    "experience": KIND_EXPERIENCE,
    "user_content": KIND_USER_CONTENT,
    "question": KIND_QUESTION,
    "answer": KIND_ANSWER,
    "none": 1.0,
}

_LENGTH_FACTORS = {
    "lt50": LEN_UNDER_50,
    "gt1200": LEN_OVER_1200,
    "none": 1.0,
}

# The policy preference family. One arm at most per query, because no policy
# sets more than one of the three flags.
_POLICY_FACTORS = {
    "prefer_experience": POL_PREFER_EXPERIENCE,
    "prefer_active_work": POL_PREFER_ACTIVE_WORK,
    "exact_question": POL_EXACT_QUESTION,
    "exact_other": POL_EXACT_OTHER,
    "none": 1.0,
}

_PROJECT_FACTORS = {
    "match": PROJECT_MATCH,
    "none": 1.0,
}

SHORT_CHARS = 50
LONG_CHARS = 1200


def kind_branch(content_kind: str | None) -> str:
    """Which content_kind arm this record takes, or "none".

    Exported because the trace harness needs the same classification to record
    an activation, and a second copy of the branch names would drift. capture.py
    reimplemented the recency ladder once already and got away with it only
    because the boundaries happened to agree.
    """
    return content_kind if content_kind in _KIND_FACTORS else "none"


def length_branch(content_length: int) -> str:
    """Which length arm this record takes, or "none".

    Two arms, not three: the additive ladder's <20 branch is gone.
    """
    if content_length < SHORT_CHARS:
        return "lt50"
    if content_length > LONG_CHARS:
        return "gt1200"
    return "none"


def recency_bucket_or_unparsed(recency_bucket: str) -> str:
    """Normalise a recency bucket name to one RECENCY carries.

    An unrecognised name means the caller and this table disagree about the
    ladder, which is a bug rather than a neutral record. It resolves to
    "unparsed" -- whose multiplier is 1.0, the same answer a `.get` default
    would give -- but as a NAMED branch, so the traffic window shows it
    happening instead of the count vanishing into the identity.
    """
    return recency_bucket if recency_bucket in RECENCY else "unparsed"


def policy_branch(
    *,
    experience_fired: bool,
    active_work_fired: bool,
    exact_branch: str,
) -> str:
    """Which policy-preference arm this record takes, or "none".

    Takes the three facts a caller actually holds, not six. A *_fired value
    already means "the policy enabled this AND the predicate matched" -- both
    callers compute it that way -- so a separate `prefer_*` argument to AND
    against would be the same conjunction twice. `exact_branch` is keyed the
    way schema.PolicyActivation records it ("question" | "other" | "none"),
    which is the exact arm domain minus the prefix, so the trace harness
    passes it through instead of decomposing it into two booleans and having
    this rebuild it.

    The three preference flags are mutually exclusive across the policy table,
    so this returns at most one arm. It is ordered rather than branching on all
    three because a policy that set two would otherwise silently take whichever
    the code checked first; here the order is explicit and the exclusivity is
    asserted by tests/test_composition_bound.py.
    """
    if experience_fired:
        return "prefer_experience"
    if active_work_fired:
        return "prefer_active_work"
    if exact_branch != "none":
        return f"exact_{exact_branch}"
    return "none"


# #250's resolution, asserted where it is derived rather than only in a test.
# A short user-authored record must come out net LIFTED: LEN_UNDER_50 is a
# discount and KIND_USER_CONTENT a boost, they sit in different families, and
# whether their product clears 1.0 depends on the family SHARES, not on the ST
# ordering alone -- the length family's downward share fell from 18% to 10% in
# the 2026-10-01 re-derivation and that is what moved it. So the next
# re-derivation is stopped here rather than discovering it in CI.
assert LEN_UNDER_50 * KIND_USER_CONTENT > 1.0, (
    f"a short user-authored record is net penalised "
    f"({LEN_UNDER_50:.6f} x {KIND_USER_CONTENT:.6f} = "
    f"{LEN_UNDER_50 * KIND_USER_CONTENT:.6f}); #250 has regressed"
)


def clamp(value: float) -> float:
    """Hold the prior inside its half of the bound.

    An ASSERTION now, not the enforcement. Under the log-space allocation the
    worst one-arm-per-family product is exactly PRIOR_MIN (and PRIOR_MAX
    upward), so no reachable combination can leave the bound and this cannot
    fire. It is retained, instrumented, and tested for unreachability, because
    a bound that holds by construction still needs something to notice when a
    future term breaks the construction.

    The counters are the record of that. If clamped_low or clamped_high ever
    fires in the traffic window, a term has been added or retuned without
    re-deriving the allocation.
    """
    count("prior.clamped_low", value < PRIOR_MIN)
    count("prior.clamped_high", value > PRIOR_MAX)
    return max(PRIOR_MIN, min(PRIOR_MAX, value))


def assemble(
    *,
    content_kind: str | None,
    content_length: int,
    recency_bucket: str,
    is_reflection: bool = False,
    policy_arm: str = "none",
    project_match: bool = False,
) -> float:
    """The metadata prior for one candidate, as a bounded multiplier.

    Query-independent in the sense that matters: nothing here compares the
    query to the record. `policy_arm` and `project_match` are conditional on
    the query's POLICY and on the active project, but neither measures
    query-record similarity -- they are class preferences whose activation is
    conditional. Terms that do measure similarity (lexical overlap, entity
    match) adjust the similarity estimate, stay at the retrieval stage, and
    are not bounded by this.
    """
    return clamp(
        unclamped(
            content_kind=content_kind,
            content_length=content_length,
            recency_bucket=recency_bucket,
            is_reflection=is_reflection,
            policy_arm=policy_arm,
            project_match=project_match,
        )
    )


def unclamped(
    *,
    content_kind: str | None,
    content_length: int,
    recency_bucket: str,
    is_reflection: bool = False,
    policy_arm: str = "none",
    project_match: bool = False,
) -> float:
    """assemble() without the clamp. The composed product itself.

    Split out for tests/test_composition_bound.py, which has to assert that no
    combination reaches the clamp -- a claim it cannot make against assemble(),
    because assemble() clamps. It previously kept its own copy of this product,
    which meant a sixth family added to assemble() and not to the copy would
    leave the central assertion passing while measuring the wrong thing.
    """
    prior = 1.0

    # Each arm is normalised to a table key first, then indexed directly. No
    # `.get(..., default)` fallbacks: the normalisers guarantee a hit, and a
    # defensive default would quietly turn a typo into the identity instead of
    # raising -- which is how an unmatched recency bucket would have become a
    # silent 1.0.
    prior *= _KIND_FACTORS[branch("prior.kind", kind_branch(content_kind))]
    prior *= _LENGTH_FACTORS[branch("prior.length", length_branch(content_length))]
    prior *= RECENCY[branch("prior.recency", recency_bucket_or_unparsed(recency_bucket))]

    prior *= _POLICY_FACTORS[branch("prior.policy", policy_arm)]
    prior *= _PROJECT_FACTORS[branch("prior.project", "match" if project_match else "none")]

    if count("prior.reflection_path", is_reflection):
        prior *= REFLECTION_DISCOUNT

    return clamp(prior)
