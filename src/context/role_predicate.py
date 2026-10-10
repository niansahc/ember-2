"""
src/context/role_predicate.py

ADR-044 amendment 4a: role leaves the scoring budget.

The role pile (-0.45 combined) was three times the fixture cosine spread
and five and a half times the measured production spread of 0.0815 -- the
largest single term in the stack, spent on one job. PR #217 measured what
that job is: the reduced arm fails self-echo the moment the pile is
removed, and a predicate over the same records suppresses it completely,
decoy gone and answer still delivered. A predicate is not an
approximation of the pile on that incident; it is a substitute for it.

So assistant-authored conversation is excluded by a WHERE clause rather
than outvoted by a constant. It costs nothing against a bounded budget,
states its intent in the query rather than in a magnitude, and cannot be
outranked by an unrelated term.

Two things 4a left open, and what this does about them.

**Exclude or cap.** 4a measured exclusion and called it the simpler
default, but noted a quota preserves access for the case where an
assistant turn is genuinely the best record, and that nothing tests that
case. This implements exclusion. The untested case stays untested and out
of scope; if it ever needs the quota, that is a change with a measurement
behind it rather than a hedge shipped in advance.

**Which column values.** 4a required the predicate be written against the
values the column actually carries rather than the ones f9f5dda assigned.
After #218 those are mixed (10,249), first_person (8,367) and unknown
(64); third_party is retired and has no rows. So the predicate keys on
metadata role, not on an authorship value that does not exist.

WHY THIS IS NOT SCOPED BY QUERY
-------------------------------
It was, briefly, and that was a defect. The first implementation excluded
assistant content only on relational queries, reasoning that UAT-005 was a
relational incident and that narrower is safer.

It is not safer, because the incident 4a actually measured is self-echo,
and the self-echo incident's query is not relational
(`_matches_relational_query` returns False for it). The gate therefore
suppressed the predicate on the one incident the amendment cites as the
one capability role had no second owner for. Relational contamination --
the incident that *is* relational -- passes in every arm 4a measured,
including the fully reduced one, because it is the authorship multiplier's
job and never was role's.

So the query class the gate keyed on was the one class that did not need
it. Exclusion is unconditional: assistant self-echo does not depend on
what was asked, and a predicate that reads the query is not a WHERE clause
on an authorship column, which is what 4a decided this should be.
"""

from __future__ import annotations

from src.observability.guard_counters import count

ASSISTANT_ROLES = frozenset({"assistant"})


def excluded_by_role(item) -> bool:
    """True when this record is assistant-authored.

    Query-independent by construction. Anything that needs to reason about
    the query is a scoring or gating concern and does not belong here.
    """
    metadata = getattr(item, "metadata", {}) or {}
    role = (metadata.get("role") or "").lower()
    return count("role_predicate.assistant_excluded", role in ASSISTANT_ROLES)


def apply(items: list) -> list:
    """Drop assistant-authored records."""
    return [i for i in items if not excluded_by_role(i)]
