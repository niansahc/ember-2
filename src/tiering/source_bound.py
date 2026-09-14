"""
src/tiering/source_bound.py

Tier-rank ordering and the source-bound cap for derived records (ADR-015
amendment, PR #180, implementation step 2).

A derived record's tier cannot exceed the tier of the records it was built
from -- derived content has no independent standing. This module owns the
one comparison the rest of the codebase never needed until now: given a set
of tier strings, which is hottest. Nothing in the repo had this before
(confirmed by search); TieringService's own tier assignment is a one-way
heat-to-tier mapping, not a tier-to-tier comparison.
"""

from __future__ import annotations

# No existing ordering constant anywhere in the repo -- this is the first.
TIER_RANK: dict[str, int] = {"cold": 0, "warm": 1, "hot": 2}


def bound_tier(
    natural_tier: str,
    source_record_ids: list[str] | None,
    tier_index: dict[str, str],
) -> str:
    """Cap natural_tier at the hottest tier among resolvable sources.

    natural_tier is whatever tier the record would otherwise get (from heat,
    for a reflection; from whatever the caller computed, for a lodestone).
    tier_index maps record id -> current tier, scoped to whatever store(s)
    the caller has already looked up.

    Legacy rule (ADR-015 amendment): no source_record_ids at all, or a
    non-empty list where NONE resolve, floors at cold -- unprovenanced
    derived content gets no benefit of the doubt.

    Partial resolution: bounds against whatever DOES resolve. A source that
    no longer resolves (deleted, suppressed) is dropped from the
    calculation, not treated as if the whole record were unprovenanced --
    those are different failure states, and only the second is what the
    floor rule is aimed at.

    The record may end up colder than its sources (bound is a ceiling, not
    a floor) -- if natural_tier is already colder than or equal to the
    source bound, it is returned unchanged.
    """
    if not source_record_ids:
        return "cold"

    resolved_tiers = [
        tier_index[source_id]
        for source_id in source_record_ids
        if source_id in tier_index
    ]
    if not resolved_tiers:
        # Every id was present in the list but none resolved to a known
        # tier -- effectively unprovenanced, same floor as the empty case.
        return "cold"

    source_bound = max(resolved_tiers, key=lambda tier: TIER_RANK[tier])
    if TIER_RANK[natural_tier] <= TIER_RANK[source_bound]:
        return natural_tier
    return source_bound
