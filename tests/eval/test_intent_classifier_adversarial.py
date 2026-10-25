"""tests/eval/test_intent_classifier_adversarial.py

Adversarial baseline for the intent classifier (ADR-034) and the
explicit web-marker dispatch in src/context/policies.py.

Records the routing verdict for queries across five categories that
have empirically misrouted in production:

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
  5. First-person schedule    - the user's own appointments, travel,
     and status                  orders and errands, which use the time
                                 and status words of a live lookup

Each test runs the live classifier (via classify_intent or
classify_query). Categories 1-3 and 5 assert on the classifier label
through _classify_answered, which also fails the case when Stage 3 timed
out or failed: the safe default is vault_answerable, the label those
categories expect, so a dead or cold model would otherwise pass them.
Category 4 asserts on the policy's use_web_search field.

This file is gated behind @pytest.mark.eval (Tier 3). It is excluded
from Tier 1 / Tier 2 and from the post-edit hook. Every case asserts on
its own, so every case must pass; there is no aggregate floor. Run it
before any change to the classifier, its example pool, or the Stage 3
prompt merges.

Recorded baseline (2026-05-13, main at commit 594af1f), kept as history:

  Category 1 (first-person recall): 4/5. Residual: "what was I nervous
    about for this weekend" mis-routed to needs_internet - same query
    as B-CTX-001 turn 9, marked xfail at the unit level in
    tests/test_intent_classifier.py.
  Category 2 (personal comparison): 5/5 via timeout-to-safe-default.
    Stage 3 sent think inside options, where Ollama ignores it, so
    qwen3:8b thought and overran the 1500ms cap on all five queries;
    the cap returned vault_answerable. PR #306 fixed the think argument.
  Category 3 (introspective uncertainty): 5/5 via Stage 2 exemplar
    anchoring (commit 4b702d3).
  Category 4 (bare-marker no content): 0/5. All five queries matched an
    _EXPLICIT_WEB_MARKERS entry and returned the web_search policy with
    use_web_search=True (the B2 misroute). PR #4 (fix/b2-bare-marker-
    clarification) routed them to a clarification policy.

Re-recorded 2026-10-24 (PR #306 plus PR #308, warm qwen3:8b): every
case in Categories 1-5 passes. Category 2 passes on Stage 3 answers,
routed by the B1 step 2 prompt; Category 4 returns the clarification
policy; Category 5 is new in PR #308.
"""

from __future__ import annotations

import logging
import sys

import pytest

import src.llm.intent_classifier as intent_classifier
from src.context.policies import classify_query
from src.llm.intent_classifier import (
    VAULT_ANSWERABLE,
    classify_intent,
)


pytestmark = pytest.mark.eval


@pytest.fixture(autouse=True)
def reset_stage2_cache():
    """Reset the Stage 2 example-embedding cache so each test starts
    with a clean slate. Same pattern as tests/test_intent_classifier.py
    and tests/eval/test_intent_classifier_calibration.py."""
    intent_classifier._example_embeddings = None
    yield
    intent_classifier._example_embeddings = None


_STAGE3_FAILED = "[INTENT_CLASSIFY] Stage 3 failed"
_warmed = False


def _classify_answered(query: str, caplog) -> str:
    """classify_intent, failing when Stage 3 timed out or failed.

    classify_intent logs one "Stage 3 failed" WARNING on that path and
    returns the safe default, which is the label Categories 1-3 and 5
    expect. The first call per process loads qwen3:8b without the
    budget, so a cold model load is not reported as a failure.
    """
    global _warmed
    if not _warmed:
        intent_classifier._stage3_llm_call("warm up")
        _warmed = True
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="ember.intent_classifier"):
        label = classify_intent(query)
    failures = [
        r.getMessage() for r in caplog.records
        if _STAGE3_FAILED in r.getMessage()
    ]
    assert not failures, (
        f"{query!r}: Stage 3 did not answer ({failures[0]}). The safe "
        f"default would pass this case without a classification."
    )
    return label


def test_answered_check_fails_when_stage3_cannot_answer(monkeypatch, caplog):
    """Positive control for _classify_answered. With Ollama unreachable
    classify_intent still returns vault_answerable, so the label alone
    would pass; the check must fail instead."""

    def _down(*args, **kwargs):
        raise ConnectionError("ollama unreachable")

    class _DownOllama:
        chat = staticmethod(_down)

    monkeypatch.setattr(intent_classifier, "ollama", _DownOllama())
    monkeypatch.setattr(intent_classifier, "embed_text", _down)
    monkeypatch.setattr(intent_classifier, "embed_texts", _down)
    monkeypatch.setattr(sys.modules[__name__], "_warmed", True)
    assert classify_intent("current job vs old job") == VAULT_ANSWERABLE
    with pytest.raises(AssertionError, match="Stage 3 did not answer"):
        _classify_answered("current job vs old job", caplog)


# ---------------------------------------------------------------------------
# Category 4: Bare/explicit marker with no content (deterministic)
# ---------------------------------------------------------------------------
# These queries match an entry in _EXPLICIT_WEB_MARKERS in
# src/context/policies.py: the user has stated the intent to search but
# provided no content for the search. Before PR #4 they returned the
# web_search policy and dispatched the bare message to SearXNG; since
# PR #4 they return a clarification policy with use_web_search=False.
#
# The marker logic is deterministic; an LLM is not involved in this
# category, so the result does not vary across runs.

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
    policy; it returns a clarification policy (use_web_search=False).

    Baseline 2026-05-13 (main at 594af1f): 0/5, the B2 misroute.
    Re-recorded 2026-10-24: 5/5."""
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
# tests/test_intent_classifier.py at the Stage 2 unit level.

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


@pytest.mark.live_ollama
@pytest.mark.parametrize("query", _CATEGORY_1_FIRST_PERSON_RECALL)
def test_category_1_first_person_recall_routes_to_vault(query, caplog):
    """First-person recall queries must route to vault_answerable. The
    full ADR-034 cascade is exercised: Stage 1 compound guard, Stage 2
    similarity, Stage 3 fallback.

    Baseline 2026-05-13 (main at 594af1f): 4/5 (residual in the module
    docstring). Re-recorded 2026-10-24: 5/5."""
    assert _classify_answered(query, caplog) == VAULT_ANSWERABLE, (
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
# 0.65 threshold, so they escalate to Stage 3, where the B1 step 2
# prompt routes them (history in the module docstring).

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


@pytest.mark.live_ollama
@pytest.mark.parametrize("query", _CATEGORY_2_PERSONAL_COMPARISON)
def test_category_2_personal_comparison_routes_to_vault(query, caplog):
    """Personal-comparison queries must route to vault_answerable,
    because the queries reference the user's own life history.

    Baseline 2026-10-24 (PR #306 plus B1 step 2): 5/5 on Stage 3
    answers."""
    assert _classify_answered(query, caplog) == VAULT_ANSWERABLE, (
        f"{query!r} mis-routed to needs_internet. Personal comparisons "
        f"reference the user's life and must route to vault_answerable. "
        f"The Stage 3 prompt (B1 step 2) may have regressed."
    )


# ---------------------------------------------------------------------------
# Category 3: Introspective uncertainty
# ---------------------------------------------------------------------------
# Phrases that sound like search queries on the surface but describe
# the user's own internal state of unresolved thought. Anchored as
# Stage 2 exemplars in commit 4b702d3 to prevent the LLM at Stage 3
# from over-interpreting "I am trying to figure that out" as a search
# intent.

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


@pytest.mark.live_ollama
@pytest.mark.parametrize("query", _CATEGORY_3_INTROSPECTIVE_UNCERTAINTY)
def test_category_3_introspective_uncertainty_routes_to_vault(query, caplog):
    """Introspective uncertainty queries must route to vault_answerable.
    These were added as Stage 2 exemplars in commit 4b702d3
    (B-WS-001) specifically to prevent surface-pattern mis-routing.

    Baseline 2026-05-13 (main at 594af1f): 5/5 via Stage 2 exemplar
    anchoring. Re-recorded 2026-10-24: 5/5."""
    assert _classify_answered(query, caplog) == VAULT_ANSWERABLE, (
        f"{query!r} mis-routed to needs_internet. Introspective "
        f"uncertainty phrases were anchored as Stage 2 exemplars in "
        f"4b702d3; mis-route here indicates the example pool has "
        f"drifted."
    )


# ---------------------------------------------------------------------------
# Category 5: First-person schedule and status
# ---------------------------------------------------------------------------
# The user's own appointments, travel, orders and errands. They carry
# the words of a live lookup (tomorrow, what time, yet, done), and before
# PR #308 four of these routed to needs_internet: "what is on my schedule
# tomorrow" at Stage 2 (an exemplar about tomorrow's rain) and three at
# Stage 3. PR #308 added first-person schedule and errand exemplars to
# the Stage 2 pool and a matching sentence to the Stage 3 prompt. The
# fixtures are not exemplars themselves, so a pass is not a lookup of
# the pool.

_CATEGORY_5_FIRST_PERSON_STATUS = [
    pytest.param("what is on my schedule tomorrow", id="schedule_tomorrow"),
    pytest.param("did my package arrive yet", id="package_arrived"),
    pytest.param("what time is my flight on friday", id="flight_friday"),
    pytest.param(
        "what time is my dentist appointment next week",
        id="dentist_appointment",
    ),
    pytest.param("when is my next appointment", id="next_appointment"),
    pytest.param("is my car repair done yet", id="car_repair_done"),
    pytest.param("has my refund come through yet", id="refund_through"),
    pytest.param("when is my rent due this month", id="rent_due"),
]


@pytest.mark.live_ollama
@pytest.mark.parametrize("query", _CATEGORY_5_FIRST_PERSON_STATUS)
def test_category_5_first_person_status_routes_to_vault(query, caplog):
    """First-person schedule and status queries must route to
    vault_answerable: the answer is in the user's own records.

    Baseline 2026-10-24 before PR #308's routing fix: 4/8 (schedule,
    package, flight, dentist routed to needs_internet). After: 8/8."""
    assert _classify_answered(query, caplog) == VAULT_ANSWERABLE, (
        f"{query!r} mis-routed to needs_internet. A query about the "
        f"user's own schedule, travel, orders or errands must route to "
        f"vault_answerable. Check the schedule and errand exemplars in "
        f"src/llm/classifier_examples.py and the Stage 3 prompt."
    )
