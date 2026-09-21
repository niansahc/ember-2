"""
tools/retrieval_trace/queries.py

The capture query set.

Coverage target is the POLICY, not the topic: every policy classify_query
can return for a vault query needs candidates traced under it, because the
policy supplies three of the parameters (memory_weight, reflection_weight,
recency_bias) and switches four more branches on and off. A sensitivity
result computed over a set that never entered `reflective` says nothing
about the weights that only `reflective` sets.

Queries are written against the marker lists in src/context/policies.py so
classification is deterministic where it can be. It cannot be everywhere:
classify_query runs the ADR-034 intent classifier before the vault policy
cascade, and that stage can route a query to web_search. Capture records
the policy that was actually assigned rather than the one intended, and the
runner reports coverage from what was observed. An intended policy that
did not materialise is a finding about the classifier, not something to
paper over by pinning the policy.

Queries are generic by construction -- no names, no vault specifics. They
have to be: this file is in the repository.
"""

from __future__ import annotations

# (query_id, query, intended policy)
QUERY_SET: tuple[tuple[str, str, str], ...] = (
    # status_state -- state_markers, matched before the intent classifier
    ("state-01", "what am i working on", "status_state"),
    ("state-02", "what are my open loops", "status_state"),
    ("state-03", "what is my current focus", "status_state"),
    ("state-04", "what are my blockers", "status_state"),
    # task_status -- task_status_markers, also ahead of the classifier
    ("task-01", "status of the current project", "task_status"),
    ("task-02", "where are we on the documentation work", "task_status"),
    ("task-03", "status update on the open work", "task_status"),
    # reflective
    ("refl-01", "what patterns have you noticed lately", "reflective"),
    ("refl-02", "what themes keep coming up for me", "reflective"),
    ("refl-03", "what trends do you notice in my work", "reflective"),
    ("refl-04", "reflect on the past few weeks", "reflective"),
    # factual_recall
    ("fact-01", "find what i said about the retrieval design", "factual_recall"),
    ("fact-02", "recall the decision about memory tiering", "factual_recall"),
    ("fact-03", "look up what i wrote about scoring", "factual_recall"),
    # recent_activity -- needs a recent marker AND an activity marker, and
    # must dodge every earlier branch in the cascade. "what have i been" is
    # a REFLECTIVE marker, so the obvious phrasing of this policy's own name
    # routes somewhere else entirely; both of these are worded around it.
    ("ract-01", "am i making progress on the migration currently", "recent_activity"),
    ("ract-02", "what i have been up to lately", "recent_activity"),
    # recent
    ("rec-01", "what was on my mind lately", "recent"),
    ("rec-02", "what did i write down today", "recent"),
    # activity
    ("act-01", "am i building anything worthwhile", "activity"),
    ("act-02", "what am i doing with my evenings", "activity"),
    # default -- no marker in any list
    ("def-01", "tell me something useful", "default"),
    ("def-02", "how do i think about tradeoffs", "default"),
    ("def-03", "what matters most to me", "default"),
    # Relational, to exercise the authorship multiplier, which is a no-op on
    # every query above. Without one of these the auth.* parameters have no
    # activation anywhere in the trace and their sensitivity is trivially zero
    # for the wrong reason.
    ("rel-01", "what do you know about my family", "default"),
    ("rel-02", "tell me about my partner", "default"),
    # Identity, to exercise the wider profile channel (limit 8, floor 0.0
    # instead of limit 3, floor 0.3) -- a different candidate population
    # rather than a different score.
    ("ident-01", "what do you know about me", "default"),
    ("ident-02", "who am i", "default"),
)

# Policies a vault query set is expected to reach. web_search and
# clarification are excluded deliberately: they route away from vault
# retrieval, so a candidate traced under them tells you about the
# classifier, not about scoring.
EXPECTED_POLICIES: frozenset[str] = frozenset(
    {
        "status_state",
        "task_status",
        "reflective",
        "factual_recall",
        "recent_activity",
        "recent",
        "activity",
        "default",
    }
)


def capture_pairs() -> list[tuple[str, str]]:
    return [(query_id, query) for query_id, query, _policy in QUERY_SET]


def intended_policy(query_id: str) -> str:
    for candidate_id, _query, policy in QUERY_SET:
        if candidate_id == query_id:
            return policy
    raise KeyError(query_id)
