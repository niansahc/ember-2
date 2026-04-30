"""tests/test_classify_query.py

Coverage for routing decisions in src/context/policies.py classify_query()
and the kinship/identity filters that complement retrieval. New file added
with the v0.17.x routing rule expansion (pets + routines).

Tests are vault-first: pet and routines queries must route to vault
retrieval channels, never to web search.
"""
from __future__ import annotations

from src.context.policies import (
    _matches_relational_query,
    classify_query,
)


# ---------------------------------------------------------------------------
# Pet / animal kinship — _matches_relational_query (filter-only)
# ---------------------------------------------------------------------------


def test_my_dog_routes_to_relational_kinship() -> None:
    """Possessive pet query matches the kinship filter so retrieval suppresses
    third-party ingested content about other people's pets."""
    assert _matches_relational_query("what breed is my dog Balor") is True


def test_my_cat_my_pet_match_kinship() -> None:
    assert _matches_relational_query("what does my cat eat") is True
    assert _matches_relational_query("what is my pet's name") is True


def test_dog_without_possessive_does_not_match() -> None:
    """Generic animal queries must NOT trigger the kinship filter — the
    `\\bmy\\s+` possessive guard is the protection against false positives."""
    assert _matches_relational_query("best dog breeds for apartments") is False
    assert _matches_relational_query("how to train a cat") is False
    assert _matches_relational_query("i'm thinking about getting a dog") is False


# ---------------------------------------------------------------------------
# Routines → status_state intent
# ---------------------------------------------------------------------------


def test_my_routine_routes_to_status_state() -> None:
    assert classify_query("what is my routine").name == "status_state"
    assert classify_query("tell me about my routines").name == "status_state"


def test_my_morning_my_schedule_my_habits_route_to_status_state() -> None:
    assert classify_query("walk me through my morning").name == "status_state"
    assert classify_query("what's my schedule today").name == "status_state"
    assert classify_query("describe my habits").name == "status_state"


def test_my_routine_does_not_trigger_web_search() -> None:
    """Hard requirement: routines queries are vault-first, never web."""
    policy = classify_query("what is my routine")
    assert policy.name != "web_search"


def test_my_dog_does_not_trigger_web_search() -> None:
    """Pet queries are vault-first, never web. classify_query routes them to
    default (the relational_query filter then suppresses third-party content
    at retrieval time)."""
    policy = classify_query("what breed is my dog")
    assert policy.name != "web_search"


# ---------------------------------------------------------------------------
# Routines vs recent — status_state must win when both could match
# ---------------------------------------------------------------------------


def test_my_routine_lately_still_routes_to_status_state() -> None:
    """`lately` is a recent_marker; `my routine` is a state_marker. Routing
    order checks status_state first, so this must land on status_state — the
    operational reading, not the recent-activity reading."""
    assert classify_query("what's my routine lately").name == "status_state"
