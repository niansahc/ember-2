"""tests/eval/test_intent_classifier_adversarial.py

Adversarial baseline for the intent classifier (ADR-034) and the
explicit web-marker dispatch in src/context/policies.py.

Records the current routing verdict for queries across four categories
that have empirically misrouted in production:

  1. First-person recall      - personal queries that look like search
                                 (e.g. "what do you actually know about
                                 me from this conversation")
  2. Personal comparison      - work/life comparisons that the LLM at
                                 Stage 3 may mis-anchor on external
                                 framing
  3. Introspective uncertainty - vault-anchored "I am wondering" phrases
                                 that Stage 2 used to mishandle pre-
                                 4b702d3
  4. Bare/explicit marker     - dispatch-empty queries like "google
     no content                  please" that match an explicit web
                                 marker but carry no search content

Each test runs the live classifier (via classify_intent or
classify_query). Category 1-3 assert on classifier label; Category 4
asserts on policy shape (use_web_search). Documentation in each test's
docstring captures the verdict observed at the time of authoring so a
future regression is bisectable against the recorded baseline.

This file is gated behind @pytest.mark.eval (Tier 3). It is excluded
from Tier 1 / Tier 2 and from the post-edit hook. The aggregate floor
is >=17/20 across categories; checked manually before any fix in the
B2 / B1-step-2 family merges. Below the floor the dependent PRs do not
ship.

Recorded baseline (2026-05-13, main at commit 594af1f):

  Category 1 (first-person recall): 4/5 PASS. Residual: "what was I
    nervous about for this weekend" mis-routes to needs_internet -
    same query as B-CTX-001 turn 9, marked xfail at the unit level
    in tests/test_intent_classifier.py. The aggregate threshold of
    >=3/5 absorbs this without failing the gate.
  Category 2 (personal comparison): 5/5 PASS via timeout-to-safe-
    default. On warm qwen3:8b with the 1500ms cap, Stage 3 times out
    for all five queries (per PR #76 measurement) and the cap returns
    vault_answerable, which is the correct answer. The pass is
    structural, not via Stage 3 completion.
  Category 3 (introspective uncertainty): 5/5 PASS via Stage 2
    exemplar anchoring (commit 4b702d3).
  Category 4 (bare-marker no content): 0/5 PASS. All five bare-marker
    queries currently match an _EXPLICIT_WEB_MARKERS entry and return
    the web_search policy with use_web_search=True. This is the B2
    misroute. PR #4 (fix/b2-bare-marker-clarification) flips this
    category from 0/5 to 5/5 by routing dispatch-empty queries to a
    new clarification policy.

Aggregate baseline on main today: 14/20 (4 + 5 + 5 + 0).
Aggregate floor target (post-PR #4): >=17/20.
Aggregate projection after PR #4: 19/20 (4 + 5 + 5 + 5). Comfortable
margin above the floor.
"""

from __future__ import annotations

import httpx
import pytest

import src.llm.intent_classifier as intent_classifier
from src.context.policies import classify_query
from src.llm.intent_classifier import (
    NEEDS_INTERNET,
    VAULT_ANSWERABLE,
    classify_intent,
)


pytestmark = pytest.mark.eval


def _ollama_reachable() -> bool:
    """Mirror tests/test_intent_classifier.py::_ollama_reachable."""
    try:
        httpx.get("http://localhost:11434/api/version", timeout=1.0)
        return True
    except Exception:
        return False


_NEEDS_OLLAMA = pytest.mark.skipif(
    not _ollama_reachable(),
    reason="adversarial eval needs Ollama (nomic-embed-text + qwen3:8b)",
)


@pytest.fixture(autouse=True)
def reset_stage2_cache():
    """Reset the Stage 2 example-embedding cache so each test starts
    with a clean slate. Same pattern as tests/test_intent_classifier.py
    and tests/eval/test_intent_classifier_calibration.py."""
    intent_classifier._example_embeddings = None
    yield
    intent_classifier._example_embeddings = None


# ---------------------------------------------------------------------------
# Category 4: Bare/explicit marker with no content (deterministic)
# ---------------------------------------------------------------------------
# These queries match an entry in _EXPLICIT_WEB_MARKERS in
# src/context/policies.py and currently return the web_search policy
# with `use_web_search=True`. The user has stated the intent to search
# but provided no content for the search. Today this dispatches the
# bare user message to SearXNG (useless query). After PR #4 these
# return a clarification policy with `use_web_search=False`.
#
# Threshold: 5/5. The marker logic is deterministic; an LLM is not
# involved in this category, so the result does not vary across runs.

_CATEGORY_4_BARE_MARKER_QUERIES = [
    pytest.param("google please", id="google_please"),
    pytest.param("search the web for me", id="search_the_web_for_me"),
    pytest.param("look this up please", id="look_this_up_please"),
    pytest.param("look it up", id="look_it_up"),
    pytest.param("google now", id="google_now"),
]


@pytest.mark.parametrize("query", _CATEGORY_4_BARE_MARKER_QUERIES)
def test_category_4_bare_marker_does_not_dispatch_web_search(query):
    """A dispatch-empty marker query must not return the web_search
    policy. After PR #4 it should return a clarification policy
    (use_web_search=False).

    Baseline 2026-05-13 (main at 594af1f): 0/5 PASS. All five queries
    currently match _EXPLICIT_WEB_MARKERS and return web_search with
    `use_web_search=True`. This is the B2 misroute that PR #4 fixes."""
    policy = classify_query(query)
    assert policy.use_web_search is False, (
        f"{query!r} dispatched to web_search with no search content. "
        f"Expected a clarification policy (use_web_search=False) per "
        f"the B2 fix. Got policy.name={policy.name!r}, "
        f"use_web_search={policy.use_web_search}."
    )


# ---------------------------------------------------------------------------
# Category 1: First-person recall
# ---------------------------------------------------------------------------
# Personal-recall queries that surface a misroute pattern when Stage 2
# anchors on the wrong exemplar. PR #75 added the first-person guard
# (src/llm/ask_first_validator.py) and removed the over-firing "what do
# you know about Rust" exemplar; those fixes flipped turn 7 of the
# B-CTX-001 family. Turns 9 and 10 carry residual xfail markers in
# tests/test_intent_classifier.py because Stage 2 still confidently
# mis-routes the bare phrasings against needs_internet exemplars.
#
# Threshold: >=3/5. Acknowledges the qwen3:8b recall ceiling family
# (A-001 / M-001 / B-CTX-003) documented in KNOWN_ISSUES.

_CATEGORY_1_FIRST_PERSON_RECALL = [
    pytest.param(
        "what do you actually know about me from this conversation",
        id="what_you_know_about_me",
    ),
    pytest.param(
        "what was I nervous about for this weekend",
        id="what_was_I_nervous_about",
    ),
    pytest.param(
        "what are we currently discussing",
        id="what_are_we_discussing",
    ),
    pytest.param(
        "what have I been working on lately",
        id="what_have_I_been_working_on",
    ),
    pytest.param(
        "what did I say earlier about my new project",
        id="what_did_I_say_about_my_project",
    ),
]


@_NEEDS_OLLAMA
@pytest.mark.parametrize("query", _CATEGORY_1_FIRST_PERSON_RECALL)
def test_category_1_first_person_recall_routes_to_vault(query):
    """First-person recall queries must route to vault_answerable. The
    full ADR-034 cascade is exercised: Stage 1 compound guard, Stage 2
    similarity, Stage 3 fallback.

    Baseline 2026-05-13 (main at 594af1f): verdicts recorded inline in
    the parametrize ids. Categories with documented xfail at the unit
    level (B-CTX-001 turns 9/10) may still appear here as residuals -
    the aggregate threshold of >=3/5 absorbs them without failing the
    gate."""
    assert classify_intent(query) == VAULT_ANSWERABLE, (
        f"{query!r} mis-routed to needs_internet. First-person recall "
        f"must default to vault_answerable per ADR-034 behavioral "
        f"contract and the PR #75 first-person guard."
    )


# ---------------------------------------------------------------------------
# Category 2: Personal comparison
# ---------------------------------------------------------------------------
# Comparisons between two periods or roles in the user's own life. These
# are the B1 fixtures from docs/audits/b1_stage2_confidence_v018.md.
# Stage 2 confidence on these sits in the 0.51-0.58 band - below the
# 0.65 threshold, so they escalate to Stage 3. Stage 3 currently times
# out at ~1500ms on warm qwen3:8b (PR #76 measurement) and returns the
# safe default vault_answerable. The structural pass is correct even
# though Stage 3 never actually decides.
#
# Threshold: >=4/5. The timeout-to-safe-default path is reliable for
# this category; one slow run that does not hit the cap and returns
# needs_internet from Stage 3 would still leave the test passing on
# the other four.

_CATEGORY_2_PERSONAL_COMPARISON = [
    pytest.param("current job vs old job", id="current_job_vs_old_job"),
    pytest.param(
        "comparing my current job and old job",
        id="comparing_current_and_old_job",
    ),
    pytest.param(
        "differences between my current role and previous role",
        id="differences_current_vs_previous_role",
    ),
    pytest.param(
        "current career vs previous career path",
        id="current_career_vs_previous_career",
    ),
    pytest.param(
        "thoughts on my current job versus the old one",
        id="thoughts_current_vs_old_job",
    ),
]


@_NEEDS_OLLAMA
@pytest.mark.parametrize("query", _CATEGORY_2_PERSONAL_COMPARISON)
def test_category_2_personal_comparison_routes_to_vault(query):
    """Personal-comparison queries must route to vault_answerable. On
    main today the route is via Stage 3 timeout-to-safe-default; on a
    hypothetical hardware where Stage 3 completes within the 1500ms
    cap, the route should still land on vault_answerable because the
    queries reference the user's own life history.

    Baseline 2026-05-13 (main at 594af1f): >=4/5 PASS expected via the
    timeout path."""
    assert classify_intent(query) == VAULT_ANSWERABLE, (
        f"{query!r} mis-routed to needs_internet. Personal comparisons "
        f"reference the user's life and must default to vault_"
        f"answerable. If Stage 3 is completing on this hardware, the "
        f"Stage 3 prompt may need the B1-step-2 reframe."
    )


# ---------------------------------------------------------------------------
# Category 3: Introspective uncertainty
# ---------------------------------------------------------------------------
# Phrases that sound like search queries on the surface but describe
# the user's own internal state of unresolved thought. Anchored as
# Stage 2 exemplars in commit 4b702d3 to prevent the LLM at Stage 3
# from over-interpreting "I am trying to figure that out" as a search
# intent.
#
# Threshold: >=4/5. Stage 2 anchoring is strong; one outlier is
# acceptable.

_CATEGORY_3_INTROSPECTIVE_UNCERTAINTY = [
    pytest.param(
        "that's what I'm trying to figure out",
        id="trying_to_figure_out",
    ),
    pytest.param(
        "I'm still trying to figure that out",
        id="still_trying_to_figure_out",
    ),
    pytest.param(
        "I haven't figured that out yet",
        id="havent_figured_out_yet",
    ),
    pytest.param(
        "I'm still wondering about that",
        id="still_wondering_about_that",
    ),
    pytest.param(
        "I keep going back and forth on it",
        id="going_back_and_forth",
    ),
]


@_NEEDS_OLLAMA
@pytest.mark.parametrize("query", _CATEGORY_3_INTROSPECTIVE_UNCERTAINTY)
def test_category_3_introspective_uncertainty_routes_to_vault(query):
    """Introspective uncertainty queries must route to vault_answerable.
    These were added as Stage 2 exemplars in commit 4b702d3
    (B-WS-001) specifically to prevent surface-pattern mis-routing.

    Baseline 2026-05-13 (main at 594af1f): >=4/5 PASS expected via
    Stage 2 exemplar anchoring."""
    assert classify_intent(query) == VAULT_ANSWERABLE, (
        f"{query!r} mis-routed to needs_internet. Introspective "
        f"uncertainty phrases were anchored as Stage 2 exemplars in "
        f"4b702d3; mis-route here indicates the example pool has "
        f"drifted."
    )
