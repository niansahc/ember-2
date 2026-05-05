"""
tests/test_web_search_freshness.py

Tests the freshness signal added to src/tools/web_search.py:

  - _parse_published_at: best-effort parse of SearXNG's publishedDate
    string (ISO 8601 + RFC 822). Returns timezone-aware UTC datetime
    or None on failure.

  - _freshness_multiplier: per-item weight in [floor, 1.0]. Undated
    items get 1.0 (neutral) so freshness only re-orders dated items
    relative to other dated items.

  - web_search: fetches CANDIDATE_POOL_SIZE results, re-ranks by
    combined SearXNG-rank + freshness, returns top MAX_RESULTS as
    dicts with title/url/snippet/published_at.

All SearXNG calls are mocked. No network access.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from src.tools.web_search import (
    CANDIDATE_POOL_SIZE,
    FRESHNESS_FLOOR,
    MAX_RESULTS,
    _freshness_multiplier,
    _parse_published_at,
    web_search,
)


# ---------------------------------------------------------------------------
# _parse_published_at
# ---------------------------------------------------------------------------

def test_parse_iso_8601_with_z_suffix():
    dt = _parse_published_at("2026-05-03T14:00:00Z")
    assert dt == datetime(2026, 5, 3, 14, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_8601_with_explicit_offset():
    dt = _parse_published_at("2026-05-03T14:00:00+00:00")
    assert dt == datetime(2026, 5, 3, 14, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_8601_naive_assumed_utc():
    dt = _parse_published_at("2026-05-03T14:00:00")
    assert dt == datetime(2026, 5, 3, 14, 0, 0, tzinfo=timezone.utc)


def test_parse_iso_8601_offset_normalized_to_utc():
    dt = _parse_published_at("2026-05-03T14:00:00-05:00")
    assert dt == datetime(2026, 5, 3, 19, 0, 0, tzinfo=timezone.utc)


def test_parse_rfc_822():
    dt = _parse_published_at("Sat, 03 May 2026 14:00:00 GMT")
    assert dt == datetime(2026, 5, 3, 14, 0, 0, tzinfo=timezone.utc)


def test_parse_empty_returns_none():
    assert _parse_published_at("") is None
    assert _parse_published_at(None) is None
    assert _parse_published_at("   ") is None


def test_parse_garbage_returns_none_and_logs(caplog):
    caplog.set_level(logging.INFO, logger="ember.web_search")
    assert _parse_published_at("not a date at all") is None
    assert any(
        "unparseable publishedDate" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# _freshness_multiplier
# ---------------------------------------------------------------------------

def test_multiplier_undated_is_neutral():
    """D-2: undated items get 1.0 so they sort by SearXNG rank only."""
    assert _freshness_multiplier(None) == 1.0


def test_multiplier_at_publish_moment_is_one():
    now = datetime(2026, 5, 3, 14, 0, 0, tzinfo=timezone.utc)
    assert _freshness_multiplier(now, now=now) == 1.0


def test_multiplier_24h_old_is_half():
    now = datetime(2026, 5, 3, 14, 0, 0, tzinfo=timezone.utc)
    published = now - timedelta(hours=24)
    assert _freshness_multiplier(published, now=now) == 0.5


def test_multiplier_decay_continues_below_floor_when_floor_zero():
    """At 48h with no floor, decay continues to 0.25 (two half-lives).
    The default floor masks this in production usage, but the math
    underneath needs to be testable."""
    now = datetime(2026, 5, 3, 14, 0, 0, tzinfo=timezone.utc)
    published = now - timedelta(hours=48)
    assert _freshness_multiplier(published, now=now, floor=0.0) == 0.25


def test_multiplier_far_in_past_floors_at_floor():
    now = datetime(2026, 5, 3, 14, 0, 0, tzinfo=timezone.utc)
    published = now - timedelta(days=365)
    assert _freshness_multiplier(published, now=now) == FRESHNESS_FLOOR


def test_multiplier_future_date_returns_one():
    """Clock skew between SearXNG result and local clock should not
    produce a multiplier above 1.0."""
    now = datetime(2026, 5, 3, 14, 0, 0, tzinfo=timezone.utc)
    published = now + timedelta(hours=2)
    assert _freshness_multiplier(published, now=now) == 1.0


# ---------------------------------------------------------------------------
# web_search re-ranking integration
# ---------------------------------------------------------------------------

def _mock_searxng_response(results):
    """Build a MagicMock SearXNG response carrying the given results list."""
    response = MagicMock()
    response.json.return_value = {"results": results}
    response.raise_for_status.return_value = None
    return response


def test_web_search_returns_max_results_with_published_at_key():
    """Every returned dict has the four expected keys, no _score leaks."""
    items = [
        {"title": f"Result {i}", "url": f"https://example.com/{i}",
         "content": "snippet", "publishedDate": "2026-05-03T14:00:00Z"}
        for i in range(CANDIDATE_POOL_SIZE)
    ]
    with patch("src.tools.web_search.requests.get",
               return_value=_mock_searxng_response(items)):
        results = web_search("test query")
    assert len(results) == MAX_RESULTS
    for item in results:
        assert set(item.keys()) == {"title", "url", "snippet", "published_at"}


def test_web_search_freshness_swaps_adjacent_dated_items():
    """SearXNG ranks a stale article at position 0 and a fresh article
    at position 1. After freshness re-ranking, the fresh article should
    rank above the stale one.

    Realistic-reach test: the combined-score formula
    (1/(rank+1)) * multiplier means freshness can swap adjacent ranks
    but cannot, at the floor of 0.3, lift a buried fresh item past
    multiple stale items at the top. This matches the design intent
    documented in Q4 of the planning grill.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    stale_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    items = [
        {"title": "Stale at top", "url": "https://example.com/stale",
         "content": "stale snippet", "publishedDate": stale_iso},
        {"title": "Fresh second", "url": "https://example.com/fresh",
         "content": "fresh snippet", "publishedDate": now_iso},
        {"title": "Filler 1", "url": "https://example.com/f1",
         "content": "filler", "publishedDate": stale_iso},
    ]
    with patch("src.tools.web_search.requests.get",
               return_value=_mock_searxng_response(items)):
        results = web_search("test query")
    titles = [r["title"] for r in results]
    assert titles.index("Fresh second") < titles.index("Stale at top")


def test_web_search_all_undated_preserves_searxng_order():
    """When no items are dated, freshness multiplier is 1.0 for all and
    the combined score reduces to SearXNG's implicit rank — order is
    preserved."""
    items = [
        {"title": f"Result {i}", "url": f"https://example.com/{i}",
         "content": "snippet"}  # no publishedDate
        for i in range(CANDIDATE_POOL_SIZE)
    ]
    with patch("src.tools.web_search.requests.get",
               return_value=_mock_searxng_response(items)):
        results = web_search("test query")
    titles = [r["title"] for r in results]
    assert titles == [f"Result {i}" for i in range(MAX_RESULTS)]
    for r in results:
        assert r["published_at"] is None


def test_web_search_undated_neutral_against_dated():
    """A fresh undated item at SearXNG rank 0 should beat a stale dated
    item at SearXNG rank 1 (multiplier 1.0 * 1.0 > 0.5 * decay)."""
    stale_iso = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    items = [
        {"title": "Undated top", "url": "https://example.com/u",
         "content": "snippet"},
        {"title": "Stale dated", "url": "https://example.com/s",
         "content": "snippet", "publishedDate": stale_iso},
    ]
    with patch("src.tools.web_search.requests.get",
               return_value=_mock_searxng_response(items)):
        results = web_search("test query")
    assert results[0]["title"] == "Undated top"


def test_web_search_returns_fewer_than_max_when_pool_small():
    """SearXNG returning < MAX_RESULTS items: return what's available."""
    items = [
        {"title": "Only result", "url": "https://example.com/o",
         "content": "snippet"}
    ]
    with patch("src.tools.web_search.requests.get",
               return_value=_mock_searxng_response(items)):
        results = web_search("test query")
    assert len(results) == 1


def test_web_search_returns_empty_on_no_results():
    with patch("src.tools.web_search.requests.get",
               return_value=_mock_searxng_response([])):
        assert web_search("nothing") == []


def test_web_search_returns_empty_on_request_failure():
    """Existing failure-mode contract: any exception returns []."""
    with patch("src.tools.web_search.requests.get",
               side_effect=ConnectionError("searxng down")):
        assert web_search("test query") == []


def test_web_search_strips_internal_score_field():
    """The _score field used during re-ranking must not leak to the caller."""
    items = [
        {"title": "Result 1", "url": "https://example.com/1",
         "content": "snippet", "publishedDate": "2026-05-03T14:00:00Z"}
    ]
    with patch("src.tools.web_search.requests.get",
               return_value=_mock_searxng_response(items)):
        results = web_search("test query")
    assert "_score" not in results[0]
