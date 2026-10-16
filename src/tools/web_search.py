import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from email.utils import parsedate_to_datetime

import requests

from src.core.config import SEARXNG_BASE_URL, get_web_search_min_term_hits

logger = logging.getLogger("ember.web_search")

SEARXNG_URL = f"{SEARXNG_BASE_URL}/search"
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


WebSearchStatus = Literal["ok", "empty", "unavailable"]

# Closed set of reasons a search yielded nothing usable. ASCII, no engine
# names, no exception text: the reason is rendered into the model-facing
# search note (src/llm/prompt_builder.py).
REASON_ENGINES_BLOCKED = "engines_blocked"
REASON_TIMEOUT = "timeout"
REASON_REQUEST_ERROR = "request_error"
REASON_NO_RESULTS = "no_results"
REASON_LOW_RELEVANCE = "low_relevance"
REASON_FILTERED = "filtered"

_STOPWORDS = frozenset(
    "the and for are but not you your was were with that this from have has "
    "had what whats who whom which when where why how about into than then "
    "them they their there here its just can could would should will did "
    "does any all out one get got let hey hello please tell give".split()
)


def _content_terms(text) -> set[str]:
    """Distinct lowercase alphanumeric terms of 3+ chars, minus stopwords."""
    return {
        t for t in re.findall(r"[a-z0-9]+", str(text or "").lower())
        if len(t) >= 3 and t not in _STOPWORDS
    }


def _all_hits_low_relevance(query, items, min_hits) -> bool:
    """True when every item shares fewer than the required query terms.

    Required terms = min_hits, or 1 when the query has fewer content terms
    than that. A query with no content terms cannot be judged, so it never
    suppresses. Only the all-low case is True; one good hit returns False.
    Logs per-hit term counts only (no hit text) for later calibration.
    """
    query_terms = _content_terms(query)
    if not query_terms or not items:
        return False
    needed = min_hits if len(query_terms) >= min_hits else 1
    counts = [
        len(query_terms & _content_terms(
            f"{item.get('title', '')} {item.get('snippet', '')}"))
        for item in items
    ]
    logger.info(
        "[WEB_SEARCH] term hits per result: %s (need %d of %d query terms)",
        counts, needed, len(query_terms),
    )
    return all(c < needed for c in counts)


@dataclass
class WebSearchResult:
    """Outcome of one SearXNG query.

    status:
      "ok"          -- at least one result.
      "empty"       -- the search ran and every engine answered, with nothing.
      "unavailable" -- the request failed, or nothing came back while one or
                       more engines were unresponsive (rate limit, CAPTCHA,
                       timeout), or every hit was rejected as irrelevant.
                       SearXNG still answers HTTP 200 in that case, so
                       `unresponsive_engines` is the only signal.
    reason: why a non-"ok" search yielded nothing (REASON_* above); None
      when the search succeeded.
    """

    items: list[dict] = field(default_factory=list)
    status: WebSearchStatus = "ok"
    reason: str | None = None


def run_web_search(query) -> WebSearchResult:
    """Query local SearXNG, re-rank by combined SearXNG-rank + freshness,
    and return the top MAX_RESULTS with a status (see WebSearchResult).

    Items are dicts with title/url/snippet/published_at; published_at is an
    ISO 8601 string (UTC) or None when SearXNG did not provide a parseable
    date. When WEB_SEARCH_MIN_TERM_HITS is set and every hit falls below it,
    the hits are dropped and the search reports unavailable/low_relevance.
    Never raises.
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

        # SearXNG lists each blocked engine as [name, reason]. ASCII-only
        # log line: engine names come from SearXNG's own engine keys.
        unresponsive = [
            str(entry[0]) for entry in data.get("unresponsive_engines", [])
            if entry
        ]
        reason = None
        if results:
            status = "ok"
            min_hits = get_web_search_min_term_hits()
            if min_hits > 0 and _all_hits_low_relevance(query, results, min_hits):
                logger.warning(
                    "[SEARCH_FAIL] low_relevance: %d hits suppressed "
                    "(min term hits %d)", len(results), min_hits,
                )
                results = []
                status = "unavailable"
                reason = REASON_LOW_RELEVANCE
        elif unresponsive:
            status = "unavailable"
            reason = REASON_ENGINES_BLOCKED
        else:
            status = "empty"
            reason = REASON_NO_RESULTS

        if reason == REASON_ENGINES_BLOCKED:
            logger.warning(
                "[WEB_SEARCH] unavailable: 0 results, unresponsive engines: %s",
                ", ".join(unresponsive),
            )
        else:
            logger.info(
                "[WEB_SEARCH] %d results (%s), unresponsive engines: %s",
                len(results), status, ", ".join(unresponsive) or "none",
            )
        return WebSearchResult(items=results, status=status, reason=reason)

    except requests.Timeout:
        logger.warning("[WEB_SEARCH] Failed: Timeout")
        return WebSearchResult(status="unavailable", reason=REASON_TIMEOUT)
    except Exception as exc:
        logger.warning("[WEB_SEARCH] Failed: %s", type(exc).__name__)
        return WebSearchResult(status="unavailable", reason=REASON_REQUEST_ERROR)


def web_search(query):
    """Result items only, [] on failure. See run_web_search for the status."""
    return run_web_search(query).items
