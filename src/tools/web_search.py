import logging

import requests

logger = logging.getLogger("ember.web_search")

SEARXNG_URL = "http://localhost:8888/search"
MAX_RESULTS = 5
SNIPPET_MAX_LEN = 200
TIMEOUT_SECONDS = 20


def web_search(query: str) -> list[dict]:
    """
    Query local SearXNG. Returns up to 5 results as dicts with title/url/snippet.
    Silent failure — returns [] on any error (SearXNG down, timeout, bad response).
    """
    try:
        response = requests.get(
            SEARXNG_URL,
            params={"q": query, "format": "json"},
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()

        results = []
        for item in data.get("results", [])[:MAX_RESULTS]:
            results.append({
                "title": item.get("title", "").strip(),
                "url": item.get("url", "").strip(),
                "snippet": item.get("content", "")[:SNIPPET_MAX_LEN].strip(),
            })

        logger.info("[WEB_SEARCH] %d results for: %s", len(results), query[:80])
        return results

    except Exception as exc:
        logger.warning("[WEB_SEARCH] Failed: %s", exc)
        return []
