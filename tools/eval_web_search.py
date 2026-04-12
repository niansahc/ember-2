"""
tools/eval_web_search.py

Automated web search accuracy evaluation for Ember-2.

Sends 30 questions requiring live web retrieval to the local Ember API,
streaming results to terminal as each completes. Checks whether web
search was triggered by looking for the X-Ember-Web-Search response
header and source citations in the response text.

Follows the same pattern as eval_manual.py --auto: uses Ember's active
model via the existing API, no separate grader call, no external API
dependency.

Requirements:
    - Ember API running at http://localhost:8000
    - SearXNG running (docker compose up)

Usage:
    python tools/eval_web_search.py                 # run with current model
    python tools/eval_web_search.py --auto-search   # bypass ask-first mode

Output:
    - stdout: per-question results streamed as they complete
    - logs/eval_web_search/eval_{timestamp}.md (metadata only)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

API_BASE = "http://localhost:8000"

# Per-question timeout — if Ember doesn't respond within this window,
# log as timeout and move on. Prevents the eval from hanging.
QUESTION_TIMEOUT = 60.0


# ---------------------------------------------------------------------------
# Test battery — 30 questions across 5 categories
# ---------------------------------------------------------------------------

TEST_QUESTIONS: list[dict] = [
    # Category 1: Current Events (6)
    {"question": "What are the top news headlines today?", "category": "current_events"},
    {"question": "What is the latest development in the US presidential race?", "category": "current_events"},
    {"question": "What major international events happened this week?", "category": "current_events"},
    {"question": "What is happening with the war in Ukraine right now?", "category": "current_events"},
    {"question": "What is the current weather forecast for New York City?", "category": "current_events"},
    {"question": "What natural disasters or extreme weather events have occurred recently?", "category": "current_events"},
    # Category 2: Science & Technology (6)
    {"question": "What is the latest breakthrough in AI research?", "category": "science_tech"},
    {"question": "What is the current status of the Artemis moon program?", "category": "science_tech"},
    {"question": "What new smartphones were announced this month?", "category": "science_tech"},
    {"question": "What is the latest news about quantum computing?", "category": "science_tech"},
    {"question": "What is the current state of self-driving car regulations in the US?", "category": "science_tech"},
    {"question": "What new features did the latest major Python release include?", "category": "science_tech"},
    # Category 3: Sports (6)
    {"question": "What are the current NBA playoff standings?", "category": "sports"},
    {"question": "Who won the most recent Formula 1 race?", "category": "sports"},
    {"question": "What are the latest Premier League results?", "category": "sports"},
    {"question": "Who is currently ranked number one in men's tennis?", "category": "sports"},
    {"question": "What major sporting events are happening this weekend?", "category": "sports"},
    {"question": "What is the latest news in MLB spring training or early season?", "category": "sports"},
    # Category 4: Business & Economics (6)
    {"question": "What is the current price of Bitcoin?", "category": "business"},
    {"question": "How did the S&P 500 perform this week?", "category": "business"},
    {"question": "What is the current US federal interest rate?", "category": "business"},
    {"question": "What major tech company layoffs or hiring announcements happened recently?", "category": "business"},
    {"question": "What is the latest US jobs report showing?", "category": "business"},
    {"question": "What major mergers or acquisitions have been announced recently?", "category": "business"},
    # Category 5: Culture (6)
    {"question": "What movies are currently number one at the box office?", "category": "culture"},
    {"question": "What are the most popular songs on the Billboard Hot 100 right now?", "category": "culture"},
    {"question": "What new TV shows premiered this month?", "category": "culture"},
    {"question": "What books are currently on the New York Times bestseller list?", "category": "culture"},
    {"question": "What major video games were released or announced recently?", "category": "culture"},
    {"question": "What are people talking about on social media today?", "category": "culture"},
]

CATEGORY_DISPLAY = {
    "current_events": "Current Events",
    "science_tech": "Science & Technology",
    "sports": "Sports",
    "business": "Business & Economics",
    "culture": "Culture",
}

# Grade labels for the eval
GRADE_LABELS = ("search_triggered", "search_not_triggered", "timeout", "error")


# ---------------------------------------------------------------------------
# API helpers
# ---------------------------------------------------------------------------


def _get_api_key() -> str:
    try:
        from src.core.config import get_ember_api_key
        key = get_ember_api_key()
        if key:
            return key
    except Exception:
        pass
    return os.getenv("EMBER_API_KEY", "")


def _headers(api_key: str) -> dict:
    h = {"Content-Type": "application/json", "X-Test-Session": "true"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _send_question(question: str, api_key: str) -> dict:
    """Send question to Ember. Returns {ok, response, latency, web_search, has_citations}."""
    try:
        t0 = time.time()
        resp = httpx.post(
            f"{API_BASE}/v1/chat/completions",
            json={
                "model": "ember-2",
                "messages": [{"role": "user", "content": question}],
                "stream": False,
            },
            headers=_headers(api_key),
            timeout=QUESTION_TIMEOUT,
        )
        latency = time.time() - t0

        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "latency": latency}

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        web_header = resp.headers.get("X-Ember-Web-Search", "false").lower() == "true"
        has_citations = _check_citations(content)

        return {
            "ok": True,
            "response": content,
            "latency": latency,
            "web_search": web_header,
            "has_citations": has_citations,
            "word_count": len(content.split()),
        }
    except httpx.TimeoutException:
        return {"ok": False, "error": "timeout", "latency": QUESTION_TIMEOUT}
    except httpx.ConnectError:
        return {"ok": False, "error": "API unreachable", "latency": 0}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:100], "latency": 0}


def _check_citations(response: str) -> bool:
    """Check if response contains web source citations (URLs or 'according to')."""
    lower = response.lower()
    return (
        "http://" in lower
        or "https://" in lower
        or "according to" in lower
        or "source:" in lower
        or "via " in lower
    )


def _set_autonomous_search(enabled: bool, api_key: str) -> bool | None:
    """Toggle web_search_autonomous preference. Returns previous value."""
    try:
        resp = httpx.get(
            f"{API_BASE}/v1/preferences",
            headers=_headers(api_key),
            timeout=10,
        )
        prev = resp.json().get("web_search_autonomous", False)
        httpx.patch(
            f"{API_BASE}/v1/preferences",
            json={"web_search_autonomous": enabled},
            headers=_headers(api_key),
            timeout=10,
        )
        return prev
    except Exception as exc:
        print(f"WARNING: Could not set autonomous search: {exc}")
        return None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Ember-2 web search accuracy eval")
    parser.add_argument("--auto-search", action="store_true",
                        help="Enable autonomous web search for the eval session")
    args = parser.parse_args()

    from tools.eval_helpers import swap_to_test_vault, restore_vault, run_cleanup

    api_key = _get_api_key()
    if not api_key:
        print("ERROR: No API key. Run scripts/set_api_key.py or set EMBER_API_KEY.")
        sys.exit(1)

    # Check API health
    try:
        health = httpx.get(f"{API_BASE}/api/health", headers=_headers(api_key), timeout=5)
        model = health.json().get("model", "unknown")
    except Exception:
        print(f"ERROR: API unreachable at {API_BASE}")
        sys.exit(1)

    # Swap to test vault for eval isolation
    previous_vault = swap_to_test_vault()

    # Auto-search toggle
    prev_autonomous = None
    if args.auto_search:
        prev_autonomous = _set_autonomous_search(True, api_key)
        print("Autonomous web search enabled for this eval session.")

    print(f"\nWeb Search Eval — {model} — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"30 questions, {QUESTION_TIMEOUT:.0f}s timeout per question")
    print("=" * 60)

    # Metadata log (no response text — vault privacy)
    log_dir = REPO_ROOT / "logs" / "eval_web_search"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    log_path = log_dir / f"eval_{timestamp}.md"
    log_lines = [
        f"# Web Search Eval — {model} — {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n",
    ]

    # Per-category tracking
    cat_results: dict[str, list[dict]] = {}
    all_latencies: list[float] = []
    counts = {g: 0 for g in GRADE_LABELS}

    try:
        for i, q in enumerate(TEST_QUESTIONS, 1):
            cat = q["category"]
            question = q["question"]
            cat_display = CATEGORY_DISPLAY.get(cat, cat)

            print(f"\n  [{i:02d}/30] {cat_display}: {question}")
            print("  Sending...", end="", flush=True)

            result = _send_question(question, api_key)
            latency = result.get("latency", 0)

            if not result["ok"]:
                error = result["error"]
                grade = "timeout" if error == "timeout" else "error"
                counts[grade] += 1
                print(f" {grade.upper()} ({latency:.1f}s) — {error}")
                log_lines.append(f"**Q{i}:** {question}\n")
                log_lines.append(f"- grade: {grade}, latency: {latency:.1f}s, error: {error}\n\n")
                cat_results.setdefault(cat, []).append({"grade": grade, "latency": latency})
                continue

            web_search = result["web_search"]
            has_citations = result["has_citations"]
            words = result["word_count"]
            triggered = web_search or has_citations
            grade = "search_triggered" if triggered else "search_not_triggered"
            counts[grade] += 1
            all_latencies.append(latency)
            cat_results.setdefault(cat, []).append({"grade": grade, "latency": latency})

            icon = "+" if triggered else "-"
            web_tag = " [web]" if web_search else ""
            cite_tag = " [cited]" if has_citations else ""
            print(f" [{icon}] {grade}{web_tag}{cite_tag} — {latency:.1f}s, {words} words")

            # Print response preview to stdout (live review)
            preview = result["response"][:200].replace("\n", " ")
            print(f"    {preview}{'...' if len(result['response']) > 200 else ''}")

            # Metadata only to log
            log_lines.append(f"**Q{i}:** {question}\n")
            log_lines.append(
                f"- grade: {grade}, web_header: {web_search}, citations: {has_citations}, "
                f"latency: {latency:.1f}s, words: {words}\n\n"
            )

    finally:
        if prev_autonomous is not None:
            _set_autonomous_search(prev_autonomous, api_key)
            print(f"\nRestored web_search_autonomous to {prev_autonomous}.")

    # Category summary
    print(f"\n{'=' * 60}")
    print("CATEGORY SUMMARY:")
    log_lines.append(f"## Summary\n\n")
    for cat_key in ("current_events", "science_tech", "sports", "business", "culture"):
        results = cat_results.get(cat_key, [])
        triggered = sum(1 for r in results if r["grade"] == "search_triggered")
        total = len(results)
        lats = [r["latency"] for r in results if r["latency"] > 0]
        avg_lat = sum(lats) / len(lats) if lats else 0
        display = CATEGORY_DISPLAY.get(cat_key, cat_key)
        line = f"  {display:25s}  {triggered}/{total} triggered  avg {avg_lat:.1f}s"
        print(line)
        log_lines.append(f"- {display}: {triggered}/{total} triggered, avg {avg_lat:.1f}s\n")

    # Overall
    total = len(TEST_QUESTIONS)
    trigger_pct = (counts["search_triggered"] / total * 100) if total else 0
    print(f"\n{'=' * 60}")
    print("OVERALL:")
    print(f"  Total: {total} questions")
    print(f"  Search triggered: {counts['search_triggered']}  Not triggered: {counts['search_not_triggered']}  "
          f"Timeouts: {counts['timeout']}  Errors: {counts['error']}")
    print(f"  Trigger rate: {trigger_pct:.0f}%")
    if all_latencies:
        print(f"  Latency — avg: {sum(all_latencies)/len(all_latencies):.1f}s  "
              f"min: {min(all_latencies):.1f}s  max: {max(all_latencies):.1f}s")

    log_lines.append(f"\n**Trigger rate:** {trigger_pct:.0f}%\n")
    if all_latencies:
        log_lines.append(
            f"**Latency:** avg {sum(all_latencies)/len(all_latencies):.1f}s, "
            f"min {min(all_latencies):.1f}s, max {max(all_latencies):.1f}s\n"
        )

    log_path.write_text("".join(log_lines), encoding="utf-8")
    print(f"\nMetadata saved to: {log_path}")
    print("(Response previews shown above — full text not written to disk)")

    # Post-run cleanup and vault restore
    run_cleanup()
    restore_vault(previous_vault)


if __name__ == "__main__":
    main()
