"""
tools/retrieval_ablation/metrics.py

Treatment-independent metrics for the retrieval-architecture ablation.

Why not tools/eval_retrieval.py::score_result: two of its four criteria are
treatment-detectors rather than quality measures.

  - `memory_type_present` is undefined under a type-off arm. It asks whether
    the winning item carries a memory_type at all, which is a property of the
    treatment, not of the answer.
  - `score_above_threshold` tests `score > 0.3` on an ABSOLUTE scale that every
    arm shifts. Removing the tier multiplier raises every score, so a type-off
    or tier-off arm scores BETTER on this criterion while returning worse
    content. An instrument that moves with the treatment cannot measure it.

Everything here is computed from the DELIVERED ORDER plus construction labels,
so no metric can read the treatment. All metrics are over `memory_items` only.

Tie handling is the reason this module is not trivial. Cold records are set to
score exactly 0.0 (src/context/ranker.py apply_policy), and Python's sort is
stable, so a block of equal-scoring items keeps its insertion order. Comparing
raw ranks across arms would then report displacement that is purely an artifact
of insertion order. Every order-sensitive comparison here is computed over tie
BANDS instead, and band sizes are reported so a large band reads as
"unresolved" rather than as agreement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping, Sequence

# Two items whose final scores differ by less than this are treated as tied.
# The pipeline's additive constants are quantised to 0.01 (0.10 type boost,
# 0.12 role, 0.18 recency), so 1e-9 would separate items that no mechanism
# meaningfully ordered. 1e-6 separates only genuinely distinct scores while
# still collapsing float noise from the multiplicative decay stage.
DEFAULT_TIE_EPSILON = 1e-6

# The service caps memory items per policy at 4-6 (service.py
# _memory_limit_for_policy). k=6 is the widest of those.
K_SERVICE = 6

# src/llm/prompt_builder.py:979 slices NON-PROFILE memory items to 4 before
# rendering, so this is what the model actually sees regardless of what the
# service delivered. Metrics at this k are computed over the non-profile slice.
K_MODEL_VISIBLE = 4


@dataclass(frozen=True)
class Delivered:
    """One item as the pipeline delivered it. Rank is list position."""

    id: str
    score: float
    memory_type: str = ""
    tier: str = "hot"


@dataclass(frozen=True)
class QueryLabels:
    """Ground truth for one query, assigned by construction.

    grades: fixture id -> relevance 0-3. Absent ids are grade 0.
    distractor_classes: fixture id -> the mechanism the distractor targets.
    abstain: True when the correct behaviour is to deliver nothing relevant.
    """

    grades: Mapping[str, int] = field(default_factory=dict)
    distractor_classes: Mapping[str, str] = field(default_factory=dict)
    abstain: bool = False

    def grade(self, item_id: str) -> int:
        return int(self.grades.get(item_id, 0))


# ---------------------------------------------------------------------------
# Graded nDCG
# ---------------------------------------------------------------------------

def _gain(grade: int) -> float:
    """Standard exponential gain. Grade 3 is worth 7, grade 1 worth 1, so the
    metric rewards putting the BEST item first rather than merely putting a
    relevant item first."""
    return (2.0 ** grade) - 1.0


def dcg(grades: Sequence[int]) -> float:
    """Discounted cumulative gain over grades already in delivered order."""
    return sum(_gain(g) / math.log2(i + 2) for i, g in enumerate(grades))


def ideal_dcg(labels: QueryLabels, k: int) -> float:
    """DCG of the best possible ordering of the whole labelled pool at k."""
    best = sorted((int(g) for g in labels.grades.values()), reverse=True)[:k]
    return dcg(best)


def ndcg_at_k(
    delivered: Sequence[Delivered],
    labels: QueryLabels,
    k: int,
) -> float | None:
    """Graded nDCG at k, or None when the metric is undefined.

    Returns None when the ideal DCG is zero, i.e. the pool contains nothing
    relevant for this query. That is the abstention case: ranking quality is
    not defined when there is nothing to rank, and abstention_correct is the
    metric that applies instead. Returning None rather than 0.0 or 1.0 keeps
    those strata out of the mean rather than silently biasing it either way.
    """
    ideal = ideal_dcg(labels, k)
    if ideal <= 0.0:
        return None
    actual = dcg([labels.grade(item.id) for item in delivered[:k]])
    return actual / ideal


def contamination_at_k(
    delivered: Sequence[Delivered],
    labels: QueryLabels,
    k: int = K_MODEL_VISIBLE,
) -> float:
    """Fraction of the top k that is grade 0 or a labelled distractor.

    This is the metric that matters for the prompt: what proportion of what the
    model is shown does not deserve to be there. Unlike nDCG it is defined even
    when nothing relevant exists, which is exactly when contamination is most
    worth knowing.
    """
    window = delivered[:k]
    if not window:
        return 0.0
    bad = sum(
        1
        for item in window
        if labels.grade(item.id) == 0 or item.id in labels.distractor_classes
    )
    return bad / len(window)


# ---------------------------------------------------------------------------
# Tie bands
# ---------------------------------------------------------------------------

def tie_band_indices(
    delivered: Sequence[Delivered],
    epsilon: float = DEFAULT_TIE_EPSILON,
) -> list[int]:
    """Band index per delivered position.

    Consecutive items whose scores differ by less than epsilon share a band.
    Items in one band were not ordered by any mechanism -- their relative
    order is whatever insertion order happened to be -- so every order-
    sensitive metric below compares bands, not raw positions.
    """
    bands: list[int] = []
    current = 0
    for i, item in enumerate(delivered):
        if i > 0 and abs(delivered[i - 1].score - item.score) >= epsilon:
            current += 1
        bands.append(current)
    return bands


def band_sizes(
    delivered: Sequence[Delivered],
    epsilon: float = DEFAULT_TIE_EPSILON,
) -> dict[int, int]:
    """Band index -> how many items share it. A large band means the arm did
    not resolve an ordering, which must be visible in the report rather than
    scored as agreement."""
    sizes: dict[int, int] = {}
    for band in tie_band_indices(delivered, epsilon):
        sizes[band] = sizes.get(band, 0) + 1
    return sizes


def unresolved_fraction(
    delivered: Sequence[Delivered],
    epsilon: float = DEFAULT_TIE_EPSILON,
) -> float:
    """Fraction of delivered items sitting in a band with more than one member."""
    if not delivered:
        return 0.0
    sizes = band_sizes(delivered, epsilon)
    tied = sum(n for n in sizes.values() if n > 1)
    return tied / len(delivered)


def boundary_tie_count(
    delivered: Sequence[Delivered],
    k: int,
    epsilon: float = DEFAULT_TIE_EPSILON,
) -> int:
    """How many items share the band that straddles the cut at k.

    When this is non-zero, which items made the cut was decided by insertion
    order rather than by score, so a Jaccard difference at that boundary is a
    tie artifact and not a treatment effect.
    """
    if not delivered or k <= 0 or k >= len(delivered):
        return 0
    bands = tie_band_indices(delivered, epsilon)
    straddling = bands[k - 1]
    if bands[k] != straddling:
        return 0
    return sum(1 for b in bands if b == straddling)


# ---------------------------------------------------------------------------
# Cross-arm comparison
# ---------------------------------------------------------------------------

def selection_jaccard(
    a: Sequence[Delivered],
    b: Sequence[Delivered],
    k: int | None = None,
) -> float:
    """Jaccard over delivered id SETS. Order-independent by construction.

    Two arms that deliver the same items in a different order score 1.0 here;
    rank_displacement is the metric that sees the reordering.
    """
    a_ids = {item.id for item in (a[:k] if k else a)}
    b_ids = {item.id for item in (b[:k] if k else b)}
    if not a_ids and not b_ids:
        return 1.0
    union = a_ids | b_ids
    if not union:
        return 1.0
    return len(a_ids & b_ids) / len(union)


def mean_pairwise_jaccard(id_sets: Sequence[Sequence[str]]) -> float | None:
    """Mean Jaccard over every unordered pair of delivered id sets.

    This is a WITHIN-arm metric and the only one here that is: every other
    comparison in this module is one arm against the reference. It answers a
    different question -- how much does this arm's delivered set change when
    the query changes -- and a high value means delivery is being decided by
    something other than the query.

    It exists because that number has been cited repeatedly without being
    computed anywhere. Issue #204's headline table reports it for seven arms;
    only the two endpoints were reproducible from the committed artifact,
    because the artifact carries selection_jaccard (arm vs reference), which
    is a different quantity that happens to share a name.

    Read it against a reference value, not against zero. An IDEAL retriever
    on this corpus scores near the uniform-random value, because the strata
    were authored with largely disjoint relevant sets -- so "low" is what
    correctness looks like HERE, and does not generalise to a real vault
    where a user's concerns recur across questions. Bare cosine and a random
    permutation are both available as reference points in the same run.

    Returns None for fewer than two sets, where the metric is undefined
    rather than zero.
    """
    sets = [set(ids) for ids in id_sets]
    if len(sets) < 2:
        return None

    scores: list[float] = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            union = sets[i] | sets[j]
            # Two empty deliveries agree completely about delivering nothing.
            # Scoring that 0.0 would read as maximal diversity.
            scores.append(1.0 if not union else len(sets[i] & sets[j]) / len(union))

    return sum(scores) / len(scores)


def rank_displacement(
    a: Sequence[Delivered],
    b: Sequence[Delivered],
    epsilon: float = DEFAULT_TIE_EPSILON,
) -> float | None:
    """Mean absolute BAND displacement over items both arms delivered.

    Band-granular, not position-granular: reordering within a tie band is
    insertion-order noise and must register as zero movement. Returns None when
    the arms share no items, where displacement is undefined.
    """
    a_bands = dict(zip([i.id for i in a], tie_band_indices(a, epsilon)))
    b_bands = dict(zip([i.id for i in b], tie_band_indices(b, epsilon)))
    common = set(a_bands) & set(b_bands)
    if not common:
        return None
    return sum(abs(a_bands[i] - b_bands[i]) for i in common) / len(common)


# ---------------------------------------------------------------------------
# Abstention, leakage, composition
# ---------------------------------------------------------------------------

def abstention_correct(
    delivered: Sequence[Delivered],
    labels: QueryLabels,
    k: int = K_MODEL_VISIBLE,
) -> bool | None:
    """On an abstain stratum, did the arm avoid presenting anything relevant-
    looking? Returns None on non-abstain strata so they stay out of the mean.

    Correct abstention means nothing above grade 0 in the model-visible window.
    Delivering low-grade filler is still a miss: the model sees it and treats
    it as memory.
    """
    if not labels.abstain:
        return None
    return all(labels.grade(item.id) == 0 for item in delivered[:k])


def leakage_by_class(
    delivered: Sequence[Delivered],
    labels: QueryLabels,
    k: int = K_MODEL_VISIBLE,
) -> dict[str, int]:
    """Distractor class -> how many of that class reached the top k.

    Distractors are constructed one per mechanism, so a non-zero count names
    the lever that failed rather than reporting undifferentiated noise.
    """
    counts: dict[str, int] = {}
    for item in delivered[:k]:
        cls = labels.distractor_classes.get(item.id)
        if cls:
            counts[cls] = counts.get(cls, 0) + 1
    return counts


def composition(delivered: Sequence[Delivered]) -> dict[str, dict[str, int]]:
    """Type and tier makeup of the delivered set.

    Reported because an arm can hold nDCG steady while changing WHAT it
    delivers -- swapping a conversation turn for an ingested chunk of equal
    grade is invisible to a ranking metric but is exactly the taxonomy
    behaviour under test.
    """
    types: dict[str, int] = {}
    tiers: dict[str, int] = {}
    for item in delivered:
        types[item.memory_type or "unknown"] = types.get(item.memory_type or "unknown", 0) + 1
        tiers[item.tier or "unknown"] = tiers.get(item.tier or "unknown", 0) + 1
    return {"by_type": types, "by_tier": tiers}


# ---------------------------------------------------------------------------
# Per-query roll-up
# ---------------------------------------------------------------------------

def evaluate_query(
    delivered: Sequence[Delivered],
    labels: QueryLabels,
    epsilon: float = DEFAULT_TIE_EPSILON,
) -> dict:
    """Every metric for one query's delivered set.

    `non_profile` is sliced for the model-visible metrics because
    prompt_builder renders profile items separately and caps only the
    remainder at 4.
    """
    non_profile = [i for i in delivered if i.memory_type != "profile"]

    return {
        "delivered_count": len(delivered),
        "ndcg_at_6": ndcg_at_k(delivered, labels, K_SERVICE),
        "ndcg_at_4_model_visible": ndcg_at_k(non_profile, labels, K_MODEL_VISIBLE),
        "contamination_at_4": contamination_at_k(non_profile, labels, K_MODEL_VISIBLE),
        "abstention_correct": abstention_correct(non_profile, labels, K_MODEL_VISIBLE),
        "leakage_by_class": leakage_by_class(non_profile, labels, K_MODEL_VISIBLE),
        "composition": composition(delivered),
        "unresolved_fraction": unresolved_fraction(delivered, epsilon),
        "boundary_ties_at_4": boundary_tie_count(non_profile, K_MODEL_VISIBLE, epsilon),
        "boundary_ties_at_6": boundary_tie_count(delivered, K_SERVICE, epsilon),
        "delivered_ids": [i.id for i in delivered],
    }


def compare_to_reference(
    arm: Sequence[Delivered],
    reference: Sequence[Delivered],
    epsilon: float = DEFAULT_TIE_EPSILON,
) -> dict:
    """Cross-arm metrics for one query against the A0_FULL reference."""
    return {
        "selection_jaccard": selection_jaccard(arm, reference),
        "selection_jaccard_at_4": selection_jaccard(arm, reference, K_MODEL_VISIBLE),
        "rank_displacement": rank_displacement(arm, reference, epsilon),
    }


def mean_ignoring_none(values) -> float | None:
    """Mean over defined values only.

    nDCG is None on abstain strata and displacement is None when arms share no
    items. Treating those as 0.0 would drag an arm's mean down for being
    correctly silent, which is the opposite of what the metric should reward.
    """
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)
