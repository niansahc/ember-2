"""tests/test_named_entity_retrieval.py

Coverage for Fix 3 (2026-04-27) — named-entity ranking discriminator.

Background: queries containing a proper noun ("what kind of dog is Balor")
were retrieving records about a different entity because the bare-term
bonus (+0.03 per term hit, capped at +0.18) was insufficient to overcome
embedding-cosine differences. Records about a more emotionally-laden
entity (richer content) ranked above records actually about the queried
entity.

Tests use synthetic placeholder names (Rex, Max, Buddy) so no vault
content appears in the fixture data.
"""
from __future__ import annotations

import pytest

from src.retrieval.semantic_search import (
    _ENTITY_NAME_BLOCKLIST,
    _ENTITY_NAME_BOOST,
    _ENTITY_NAME_CAP,
    _extract_entity_names,
    lexical_relevance_bonus,
)


# ---------------------------------------------------------------------------
# _extract_entity_names — proper-noun detection
# ---------------------------------------------------------------------------


def test_extract_entity_names_returns_empty_for_empty_query():
    assert _extract_entity_names("") == []
    assert _extract_entity_names(None) == []  # type: ignore[arg-type]


def test_extract_entity_names_picks_up_proper_noun_in_middle():
    """Capitalized 3+ char token in the middle of a query is treated as a name."""
    names = _extract_entity_names("what breed is Rex")
    assert "rex" in names


def test_extract_entity_names_skips_sentence_initial_word():
    """The first word is capitalized in any sentence — that's grammar, not
    a name signal. Skip it."""
    names = _extract_entity_names("Rex is sleeping")
    assert "rex" not in names


def test_extract_entity_names_skips_after_sentence_terminator():
    """A capitalized word right after `.` or `?` is also sentence-initial."""
    names = _extract_entity_names("Tell me about my pet. Buddy is sweet.")
    # "Buddy" follows ". " — sentence-initial, not a name signal.
    assert "buddy" not in names


def test_extract_entity_names_filters_pronouns_and_question_words():
    """`I`, `My`, `What`, etc. capitalize for grammar reasons."""
    names = _extract_entity_names("I have a friend named Max and What is his story")
    assert "max" in names
    # All blocklist words excluded
    for forbidden in ("I", "My", "What"):
        assert forbidden.lower() not in names


def test_extract_entity_names_filters_kinship_nouns():
    """Kinship nouns (Dog, Cat, Friend) are already handled by the
    possessive-marker logic in policies.py — don't double-boost them
    as named entities."""
    names = _extract_entity_names("My friend Sam has a Dog named Buddy")
    assert "sam" in names
    assert "buddy" in names
    # Dog is a kinship noun — must NOT be in entity list
    assert "dog" not in names


def test_extract_entity_names_returns_lowercase():
    names = _extract_entity_names("the breed of Buddy and Rex")
    assert all(n == n.lower() for n in names)


def test_extract_entity_names_requires_min_length():
    """Length ≥ 3 — short capitalized tokens like 'Hi' aren't proper nouns."""
    names = _extract_entity_names("yes Hi Bo is here")
    # "Bo" is too short (2 chars) → skipped. "Hi" is also too short.
    assert names == []


def test_extract_entity_names_blocklist_stays_in_sync():
    """Sanity: every blocklist entry is properly cased (Title-cased single
    word) so the regex would actually match before the filter applies."""
    for token in _ENTITY_NAME_BLOCKLIST:
        # Tokens may be contractions ("I'm", "I've") or single words
        first_char = token[0]
        assert first_char.isupper(), token


# ---------------------------------------------------------------------------
# lexical_relevance_bonus — entity boost
# ---------------------------------------------------------------------------


def test_entity_boost_fires_when_name_in_content():
    """A query naming Rex against a record about Rex earns the entity boost
    on top of the term-hit bonus."""
    bonus = lexical_relevance_bonus(
        normalized_query="what breed is rex",
        query_terms=["breed", "rex"],
        normalized_content="rex is a labrador retriever",
        raw_query="what breed is Rex",
    )
    # Term hit on "rex" gives +0.03; entity boost adds +0.20.
    assert bonus >= _ENTITY_NAME_BOOST


def test_entity_boost_not_applied_when_name_absent_from_content():
    """A query naming Rex but a record about Max — no entity boost."""
    bonus_with_name = lexical_relevance_bonus(
        normalized_query="what breed is rex",
        query_terms=["breed", "rex"],
        normalized_content="max is a golden retriever",  # no 'rex'
        raw_query="what breed is Rex",
    )
    # Without the entity match, bonus is just term-hit on "breed" (+0.03).
    assert bonus_with_name < _ENTITY_NAME_BOOST


def test_entity_boost_makes_correct_record_outrank_richer_one():
    """The full B-NAMED-001 regression: Rex's sparse record should
    outrank a richer record about Buddy when the query asks about Rex."""
    rex_score = lexical_relevance_bonus(
        normalized_query="what breed is rex",
        query_terms=["breed", "rex"],
        normalized_content="rex is a beagle",
        raw_query="what breed is Rex",
    )
    buddy_score = lexical_relevance_bonus(
        normalized_query="what breed is rex",
        query_terms=["breed", "rex"],
        # Richer content but doesn't mention Rex
        normalized_content=(
            "buddy was a wonderful companion and i still miss him every day. "
            "he was a corgi who loved long walks and afternoon naps."
        ),
        raw_query="what breed is Rex",
    )
    assert rex_score > buddy_score


def test_entity_boost_capped_at_total():
    """Multiple entity matches are capped so a record with many name
    mentions can't dominate via name-spam alone."""
    bonus = lexical_relevance_bonus(
        normalized_query="rex max buddy and luna",
        query_terms=["rex", "max", "buddy", "luna"],
        # Content contains all four names
        normalized_content="i have rex max buddy and luna in the kennel",
        raw_query="Rex Max Buddy and Luna",  # 4 entity matches
    )
    # 4 entities × 0.20 = 0.80, but cap is 0.40
    # Plus term hits cap at 0.18 and full-query exact-match +0.10.
    # Total bonus must be ≤ 0.10 + 0.18 + 0.40 = 0.68
    assert bonus <= 0.68 + 1e-6  # small float tolerance


def test_entity_boost_skipped_when_raw_query_not_provided():
    """Backward compat: callers that don't pass raw_query don't get the
    entity branch (no errors, just no bonus)."""
    bonus = lexical_relevance_bonus(
        normalized_query="rex",
        query_terms=["rex"],
        normalized_content="rex is here",
        # raw_query not provided
    )
    # Just term-hit bonus, no entity boost
    assert bonus < _ENTITY_NAME_BOOST


def test_entity_boost_does_not_fire_on_lowercase_query():
    """A query with no capitalized words can't have proper nouns. The
    boost is exclusive to capitalized signal."""
    bonus = lexical_relevance_bonus(
        normalized_query="what breed is rex",
        query_terms=["breed", "rex"],
        normalized_content="rex is a beagle",
        raw_query="what breed is rex",  # lowercase!
    )
    # No capitalized name in raw_query → no entity boost
    # Just term hits + maybe full-query match (rex doesn't appear at start)
    assert bonus < _ENTITY_NAME_BOOST


def test_entity_boost_skips_blocklisted_capitalized_words():
    """`What`, `My`, `I` etc. are capitalized but not entity signals.
    A record matching only those words does NOT get the boost."""
    bonus = lexical_relevance_bonus(
        normalized_query="my schedule",
        query_terms=["my", "schedule"],
        normalized_content="my schedule for the week",
        raw_query="My schedule",  # "My" is sentence-initial AND blocklisted
    )
    # No entity match — "My" is filtered out
    assert bonus < _ENTITY_NAME_BOOST