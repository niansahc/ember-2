import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

logger = logging.getLogger("ember.web_search")

SEARXNG_URL = "http://localhost:8888/search"
# Internal candidate pool fetched from SearXNG before re-ranking. Caller-
# visible result count stays at MAX_RESULTS so prompt-section size and
# every downstream consumer is unaffected.
CANDIDATE_POOL_SIZE = 10
MAX_RESULTS = 5
SNIPPET_MAX_LEN = 200
TIMEOUT_SECONDS = 20
# Freshness decay: dated items lose half their weight every 24 hours,
# floored so even a year-old article retains some weight rather than
# dropping to zero. Tunable per-deployment if eval shows the half-life
# is wrong for typical Ember queries.
FRESHNESS_HALF_LIFE_HOURS = 24
FRESHNESS_FLOOR = 0.3


def _parse_published_at(raw):
    """Best-effort parse of SearXNG's publishedDate.

    Tries ISO 8601 first (covers most engines on Python 3.12), then
    RFC 822 via email.utils (covers news engines that emit Atom/RSS-style
    dates). Returns timezone-aware UTC datetime, or None on any failure.
    INFO-logs once per failure so unknown formats can be surfaced and
    added to the parser later without crashing the search.
    """
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    logger.info("[WEB_SEARCH] unparseable publishedDate: %r", s[:50])
    return None


def _freshness_multiplier(
    published_at,
    now=None,
    half_life_hours=FRESHNESS_HALF_LIFE_HOURS,
    floor=FRESHNESS_FLOOR,
):
    """Compute a freshness weight in [floor, 1.0].

    Undated items return 1.0 (neutral) so the freshness signal only
    re-orders dated items relative to other dated items. Future dates
    (clock skew between SearXNG result and local clock) also return 1.0
    rather than producing a negative-age multiplier above 1.0.
    """
    if published_at is None:
        return 1.0
    if now is None:
        now = datetime.now(timezone.utc)
    age_hours = (now - published_at).total_seconds() / 3600
    if age_hours <= 0:
        return 1.0
    decay = 0.5 ** (age_hours / half_life_hours)
    return max(floor, decay)


def web_search(query):
    """Query local SearXNG, re-rank by combined SearXNG-rank + freshness,
    return top MAX_RESULTS as dicts with title/url/snippet/published_at.

    published_at is an ISO 8601 string (UTC) or None when SearXNG did not
    provide a parseable date. Silent failure on the request itself:
    returns [] on any exception.
    """
    try:
        response = requests.get(
            SEARXNG_URL,
            params={"q": query, "format": "json"},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()

        candidates = []
        now = datetime.now(timezone.utc)
        for rank, item in enumerate(data.get("results", [])[:CANDIDATE_POOL_SIZE]):
            published_at = _parse_published_at(item.get("publishedDate"))
            multiplier = _freshness_multiplier(published_at, now=now)
            score = (1.0 / (rank + 1)) * multiplier
            candidates.append({
                "title": item.get("title", "").strip(),
                "url": item.get("url", "").strip(),
                "snippet": item.get("content", "")[:SNIPPET_MAX_LEN].strip(),
                "published_at": published_at.isoformat() if published_at else None,
                "_score": score,
            })

        candidates.sort(key=lambda d: d["_score"], reverse=True)
        results = [
            {k: v for k, v in c.items() if k != "_score"}
            for c in candidates[:MAX_RESULTS]
        ]

        logger.info("[WEB_SEARCH] %d results for: %s", len(results), query[:80])
        return results

    except Exception as exc:
        logger.warning("[WEB_SEARCH] Failed: %s", exc)
        return []
