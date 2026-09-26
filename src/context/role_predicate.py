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
case. This implements exclusion on relational queries only -- the class
of query UAT-005 was about -- and leaves assistant content competing
normally everywhere else. That is narrower than a global exclusion and
keeps the untested case out of scope.

**Which column values.** 4a required the predicate be written against the
values the column actually carries rather than the ones f9f5dda assigned.
After #218 those are mixed (10,249), first_person (8,367) and unknown
(64); third_party is retired and has no rows. So the predicate keys on
metadata role, not on an authorship value that does not exist.
"""

from __future__ import annotations

from src.observability.guard_counters import count

ASSISTANT_ROLES = frozenset({"assistant"})


def excluded_by_role(item, user_message: str) -> bool:
    """True when this record is assistant-authored and the query is relational.

    Relational queries are the ones where an assistant turn answering as
    though it were the user is the documented failure. Everywhere else
    assistant content is ordinary context and competes on its merits.
    """
    from src.context.policies import _matches_relational_query

    if not count("role_predicate.relational_query",
                 bool(_matches_relational_query(user_message))):
        return False

    metadata = getattr(item, "metadata", {}) or {}
    role = (metadata.get("role") or "").lower()
    return count("role_predicate.assistant_excluded", role in ASSISTANT_ROLES)


def apply(items: list, user_message: str) -> list:
    """Drop assistant-authored records on a relational query."""
    return [i for i in items if not excluded_by_role(i, user_message)]
