"""tests/test_clarification_policy.py

Unit tests for the _is_bare_marker_query helper in src/context/policies.py.

The helper decides whether an explicit web-marker query carries any
actual search content. A marker match without content (e.g.,
"google please") routes to a clarification policy instead of
dispatching a useless bare query to SearXNG.

Contract: given the user query and the matched marker substring,
return True if the residual after the marker strip is either empty or
made entirely of META_PHRASES tokens (politeness/filler).
"""

from __future__ import annotations

from src.context.policies import _is_bare_marker_query


def test_marker_followed_by_single_meta_phrase_is_bare():
    """Tracer bullet: the canonical B2 failure case.

    'google please' was the seed example throughout the design grill.
    After stripping the 'google' marker the residual is 'please', a
    META_PHRASES member, so the helper must report this as bare."""
    assert _is_bare_marker_query("google please", "google") is True


def test_marker_followed_by_content_is_not_bare():
    """A marker with real search content must NOT be flagged bare.

    'google iPhone 16 release' has 'iPhone', '16', 'release' in the
    residual - none are META_PHRASES tokens, so the helper must let
    this dispatch normally."""
    assert _is_bare_marker_query("google iphone 16 release", "google") is False


def test_marker_alone_with_no_residual_is_bare():
    """A query that is exactly the marker (or marker + whitespace) has
    an empty residual after the strip - clearly nothing to search for."""
    assert _is_bare_marker_query("google", "google") is True
    assert _is_bare_marker_query("google ", "google") is True


def test_multi_word_meta_phrase_residual_is_bare():
    """Multi-word META_PHRASES entries like 'for me' must be matched
    as a single phrase, not split across single-token checks.

    Without greedy multi-word matching, 'for me' would fail because
    bare 'for' is not in META_PHRASES."""
    assert _is_bare_marker_query("google for me", "google") is True
    assert _is_bare_marker_query("look this up for me", "look this up") is True


def test_punctuation_does_not_defeat_meta_match():
    """Punctuation around the residual must not block the META_PHRASES
    match. 'google please.' and 'google please!' should both be bare."""
    assert _is_bare_marker_query("google please.", "google") is True
    assert _is_bare_marker_query("google please!", "google") is True
    assert _is_bare_marker_query("google, please", "google") is True


def test_mixed_meta_and_content_tokens_is_not_bare():
    """When the residual has at least one non-META token, the query is
    real - even if the rest is filler.

    'google for me today' has 'today' as content; the helper must let
    this dispatch normally. Same for 'google please tomorrow'."""
    assert _is_bare_marker_query("google for me today", "google") is False
    assert _is_bare_marker_query("google please tomorrow", "google") is False
    assert _is_bare_marker_query("google news please", "google") is False
