"""
tools/eval_web_search.py

Automated web search accuracy evaluation for Ember-2.

Sends 30 questions requiring live web retrieval across 5 categories
(6 each) to the local Ember API, then grades each response via Claude
for factual accuracy. Outputs per-question results, category summaries,
and an overall score.

Requirements:
    - Ember API running at http://localhost:8000
    - ANTHROPIC_API_KEY set in environment
    - pip install anthropic httpx

Usage:
    python tools/eval_web_search.py                         # default qwen3:8b
    python tools/eval_web_search.py --model qwen3:8b        # explicit model
    python tools/eval_web_search.py --auto-search            # bypass ask-first mode
    python tools/eval_web_search.py --compare                # side-by-side qwen3:8b vs claude-haiku-4-5

Output:
    - stdout: per-question results + summary
    - logs/eval_web_search/eval_{model}_{timestamp}.log
"""

from __future__ import annotations

import argparse
import json
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


# ---------------------------------------------------------------------------
# Test battery — 30 questions across 5 categories
# ---------------------------------------------------------------------------

TEST_QUESTIONS: list[dict] = [
    # Category 1: Current Events (6)
    {
        "question": "What are the top news headlines today?",
        "expected_answer_hint": "Should reference real current events from today's date, not generic statements.",
        "as_of": "2026-04-12",
        "category": "current_events",
    },
    {
        "question": "What is the latest development in the US presidential race?",
        "expected_answer_hint": "Should reference specific candidates, polls, or events from 2026.",
        "as_of": "2026-04-12",
        "category": "current_events",
    },
    {
        "question": "What major international events happened this week?",
        "expected_answer_hint": "Should cite specific events from the current week with names and locations.",
        "as_of": "2026-04-12",
        "category": "current_events",
    },
    {
        "question": "What is happening with the war in Ukraine right now?",
        "expected_answer_hint": "Should reference recent developments, not just general background.",
        "as_of": "2026-04-12",
        "category": "current_events",
    },
    {
        "question": "What is the current weather forecast for New York City?",
        "expected_answer_hint": "Should include specific temperatures or conditions for today/this week.",
        "as_of": "2026-04-12",
        "category": "current_events",
    },
    {
        "question": "What natural disasters or extreme weather events have occurred recently?",
        "expected_answer_hint": "Should cite specific events with locations and dates from the past week.",
        "as_of": "2026-04-12",
        "category": "current_events",
    },

    # Category 2: Science & Technology (6)
    {
        "question": "What is the latest breakthrough in AI research?",
        "expected_answer_hint": "Should reference a specific paper, company, or announcement from 2026.",
        "as_of": "2026-04-12",
        "category": "science_tech",
    },
    {
        "question": "What is the current status of the Artemis moon program?",
        "expected_answer_hint": "Should reference the current mission status or next planned launch date.",
        "as_of": "2026-04-12",
        "category": "science_tech",
    },
    {
        "question": "What new smartphones were announced this month?",
        "expected_answer_hint": "Should cite specific device names, manufacturers, and features.",
        "as_of": "2026-04-12",
        "category": "science_tech",
    },
    {
        "question": "What is the latest news about quantum computing?",
        "expected_answer_hint": "Should reference a specific company or research result from recent months.",
        "as_of": "2026-04-12",
        "category": "science_tech",
    },
    {
        "question": "What is the current state of self-driving car regulations in the US?",
        "expected_answer_hint": "Should cite specific states, legislation, or NHTSA actions.",
        "as_of": "2026-04-12",
        "category": "science_tech",
    },
    {
        "question": "What new features did the latest major Python release include?",
        "expected_answer_hint": "Should reference a specific Python version and its features.",
        "as_of": "2026-04-12",
        "category": "science_tech",
    },

    # Category 3: Sports (6)
    {
        "question": "What are the current NBA playoff standings?",
        "expected_answer_hint": "Should list specific teams and their records or seeds.",
        "as_of": "2026-04-12",
        "category": "sports",
    },
    {
        "question": "Who won the most recent Formula 1 race?",
        "expected_answer_hint": "Should name the driver, team, and race location.",
        "as_of": "2026-04-12",
        "category": "sports",
    },
    {
        "question": "What are the latest Premier League results?",
        "expected_answer_hint": "Should cite specific match scores and team names.",
        "as_of": "2026-04-12",
        "category": "sports",
    },
    {
        "question": "Who is currently ranked number one in men's tennis?",
        "expected_answer_hint": "Should name a specific player with their current ranking points or recent results.",
        "as_of": "2026-04-12",
        "category": "sports",
    },
    {
        "question": "What major sporting events are happening this weekend?",
        "expected_answer_hint": "Should list specific events, venues, and dates.",
        "as_of": "2026-04-12",
        "category": "sports",
    },
    {
        "question": "What is the latest news in MLB spring training or early season?",
        "expected_answer_hint": "Should reference specific teams, players, or game results.",
        "as_of": "2026-04-12",
        "category": "sports",
    },

    # Category 4: Business & Economics (6)
    {
        "question": "What is the current price of Bitcoin?",
        "expected_answer_hint": "Should give a specific dollar amount or narrow range, not a range from months ago.",
        "as_of": "2026-04-12",
        "category": "business",
    },
    {
        "question": "How did the S&P 500 perform this week?",
        "expected_answer_hint": "Should cite specific index values or percentage changes.",
        "as_of": "2026-04-12",
        "category": "business",
    },
    {
        "question": "What is the current US federal interest rate?",
        "expected_answer_hint": "Should state the specific rate or range set by the Fed.",
        "as_of": "2026-04-12",
        "category": "business",
    },
    {
        "question": "What major tech company layoffs or hiring announcements happened recently?",
        "expected_answer_hint": "Should cite specific companies, numbers, and dates.",
        "as_of": "2026-04-12",
        "category": "business",
    },
    {
        "question": "What is the latest US jobs report showing?",
        "expected_answer_hint": "Should cite specific numbers (jobs added, unemployment rate).",
        "as_of": "2026-04-12",
        "category": "business",
    },
    {
        "question": "What major mergers or acquisitions have been announced recently?",
        "expected_answer_hint": "Should name specific companies and deal values.",
        "as_of": "2026-04-12",
        "category": "business",
    },

    # Category 5: Culture (6)
    {
        "question": "What movies are currently number one at the box office?",
        "expected_answer_hint": "Should name a specific film and its opening or current weekend gross.",
        "as_of": "2026-04-12",
        "category": "culture",
    },
    {
        "question": "What are the most popular songs on the Billboard Hot 100 right now?",
        "expected_answer_hint": "Should name specific songs and artists.",
        "as_of": "2026-04-12",
        "category": "culture",
    },
    {
        "question": "What new TV shows premiered this month?",
        "expected_answer_hint": "Should cite specific show names, networks, and premiere dates.",
        "as_of": "2026-04-12",
        "category": "culture",
    },
    {
        "question": "What books are currently on the New York Times bestseller list?",
        "expected_answer_hint": "Should name specific titles and authors.",
        "as_of": "2026-04-12",
        "category": "culture",
    },
    {
        "question": "What major video games were released or announced recently?",
        "expected_answer_hint": "Should cite specific game titles, studios, and platforms.",
        "as_of": "2026-04-12",
        "category": "culture",
    },
    {
        "question": "What are people talking about on social media today?",
        "expected_answer_hint": "Should reference specific trending topics, hashtags, or viral events.",
        "as_of": "2026-04-12",
        "category": "culture",
    },
]

CATEGORY_DISPLAY = {
    "current_events": "Current Events",
    "science_tech": "Science & Technology",
    "sports": "Sports",
    "business": "Business & Economics",
    "culture": "Culture",
}


# ---------------------------------------------------------------------------
# Grade labels
# ---------------------------------------------------------------------------

GRADE_LABELS = ("accurate", "partial", "hallucination", "search_not_triggered")


# ---------------------------------------------------------------------------
# API interaction
# ---------------------------------------------------------------------------

EMBER_API_URL = "http://localhost:8000/v1/chat/completions"
CLAUDE_MODEL = "claude-sonnet-4-20250514"


def _get_ember_api_key() -> str:
    try:
        from src.core.config import get_ember_api_key
        key = get_ember_api_key()
        if key:
            return key
    except Exception:
        pass
    return os.getenv("EMBER_API_KEY", "")


def _ember_headers() -> dict:
    headers = {"Content-Type": "application/json", "X-Test-Session": "true"}
    key = _get_ember_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def send_to_ember(question: str) -> dict:
    """Send a question to Ember and return {ok, response, latency, web_search_used}."""
    try:
        t0 = time.perf_counter()
        resp = httpx.post(
            EMBER_API_URL,
            json={
                "model": "ember",
                "messages": [{"role": "user", "content": question}],
                "stream": False,
            },
            headers=_ember_headers(),
            timeout=120.0,
        )
        latency = time.perf_counter() - t0

        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}", "latency": latency}

        data = resp.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        web_used = resp.headers.get("X-Ember-Web-Search", "false").lower() == "true"
        return {"ok": True, "response": content, "latency": latency, "web_search_used": web_used}

    except Exception as exc:
        return {"ok": False, "error": str(exc), "latency": 0}


def grade_with_claude(question: str, answer: str, expected_hint: str) -> dict:
    """Grade Ember's answer via Claude. Returns {ok, grade, reason}."""
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"ok": False, "error": "ANTHROPIC_API_KEY not set"}

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        prompt = (
            f"Question asked: {question}\n"
            f"Answer given: {answer}\n"
            f"Expected answer hint: {expected_hint}\n\n"
            "Grade the answer as one of:\n"
            "- accurate: answer contains specific, verifiable facts that match the question's time frame\n"
            "- partial: answer has some relevant information but is vague or incomplete\n"
            "- hallucination: answer asserts specific facts that appear fabricated\n"
            "- search_not_triggered: answer says it doesn't have current information, suggests searching, "
            "or gives only general background without current data\n\n"
            "Return ONLY valid JSON:\n"
            '{"grade": "accurate|partial|hallucination|search_not_triggered", "reason": "one sentence"}'
        )

        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            system=(
                "You are grading an AI assistant's ability to answer questions "
                "that require live web search. Be strict — generic background "
                "knowledge does not count as accurate for time-sensitive questions."
            ),
            messages=[{"role": "user", "content": prompt}],
        )

        import re
        raw = msg.content[0].text
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            return {"ok": False, "error": f"No JSON in grader response: {raw[:200]}"}

        data = json.loads(json_match.group())
        grade = data.get("grade", "").lower()
        if grade not in GRADE_LABELS:
            grade = "hallucination"
        return {"ok": True, "grade": grade, "reason": data.get("reason", "")}

    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Model and preference management
# ---------------------------------------------------------------------------


def switch_model(model: str) -> str | None:
    """Switch Ember's active model. Returns previous model or None."""
    try:
        headers = _ember_headers()
        resp = httpx.get("http://localhost:8000/model", headers=headers, timeout=10)
        previous = resp.json().get("model", "")
        httpx.post(
            "http://localhost:8000/model",
            json={"model": model},
            headers=headers,
            timeout=10,
        )
        return previous
    except Exception as exc:
        print(f"WARNING: Could not switch model: {exc}")
        return None


def set_autonomous_search(enabled: bool) -> bool | None:
    """Toggle web_search_autonomous preference. Returns previous value."""
    try:
        headers = _ember_headers()
        resp = httpx.get("http://localhost:8000/v1/preferences", headers=headers, timeout=10)
        prev = resp.json().get("web_search_autonomous", False)
        httpx.patch(
            "http://localhost:8000/v1/preferences",
            json={"web_search_autonomous": enabled},
            headers=headers,
            timeout=10,
        )
        return prev
    except Exception as exc:
        print(f"WARNING: Could not set autonomous search: {exc}")
        return None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_eval(model_label: str) -> tuple[list[dict], str]:
    """Run all 30 questions. Returns (results, summary_text)."""
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    lines: list[str] = []
    results: list[dict] = []

    lines.append(f"Ember-2 Web Search Accuracy Evaluation — {timestamp}")
    lines.append(f"Model: {model_label}")
    lines.append(f"Grader: {CLAUDE_MODEL}")
    lines.append(f"{'=' * 60}")
    lines.append("")

    category_grades: dict[str, list[str]] = {}
    category_latencies: dict[str, list[float]] = {}
    all_latencies: list[float] = []
    counts = {g: 0 for g in GRADE_LABELS}
    counts["error"] = 0

    for i, q in enumerate(TEST_QUESTIONS, 1):
        cat = q["category"]
        question = q["question"]
        hint = q["expected_answer_hint"]
        cat_display = CATEGORY_DISPLAY.get(cat, cat)

        lines.append(f"[{i:02d}/{len(TEST_QUESTIONS)}] {cat_display}: \"{question}\"")

        ember_result = send_to_ember(question)
        latency = ember_result.get("latency", 0)

        if not ember_result["ok"]:
            lines.append(f"  ERROR — Ember: {ember_result['error']}")
            counts["error"] += 1
            results.append({"category": cat, "question": question, "grade": "error",
                            "error": ember_result["error"], "latency": latency})
            lines.append("")
            continue

        ember_response = ember_result["response"]
        web_used = ember_result.get("web_search_used", False)
        all_latencies.append(latency)
        category_latencies.setdefault(cat, []).append(latency)

        grade_result = grade_with_claude(question, ember_response, hint)
        if not grade_result["ok"]:
            lines.append(f"  ERROR — Grader: {grade_result['error']}")
            counts["error"] += 1
            results.append({"category": cat, "question": question, "grade": "error",
                            "error": grade_result["error"], "latency": latency})
            lines.append("")
            continue

        grade = grade_result["grade"]
        reason = grade_result["reason"]
        counts[grade] = counts.get(grade, 0) + 1
        category_grades.setdefault(cat, []).append(grade)

        icon = {
            "accurate": "+", "partial": "~",
            "hallucination": "!", "search_not_triggered": "-",
        }.get(grade, "?")
        web_tag = " [web]" if web_used else ""

        lines.append(f"  [{icon}] {grade.upper()}{web_tag} — {latency:.1f}s")
        lines.append(f"      {reason}")

        results.append({
            "category": cat, "question": question, "grade": grade,
            "reason": reason, "web_search_used": web_used, "latency": latency,
        })
        lines.append("")

    # Category summaries
    lines.append(f"{'=' * 60}")
    lines.append("CATEGORY SCORES:")
    for cat_key in ("current_events", "science_tech", "sports", "business", "culture"):
        grades = category_grades.get(cat_key, [])
        accurate = sum(1 for g in grades if g == "accurate")
        total = len(grades)
        pct = (accurate / total * 100) if total else 0
        lat = category_latencies.get(cat_key, [])
        avg_lat = sum(lat) / len(lat) if lat else 0
        display = CATEGORY_DISPLAY.get(cat_key, cat_key)
        lines.append(f"  {display:25s}  {accurate}/{total} accurate ({pct:.0f}%)  avg {avg_lat:.1f}s")

    # Latency summary
    if all_latencies:
        lines.append("")
        lines.append("LATENCY:")
        lines.append(f"  Average: {sum(all_latencies) / len(all_latencies):.1f}s")
        lines.append(f"  Fastest: {min(all_latencies):.1f}s")
        lines.append(f"  Slowest: {max(all_latencies):.1f}s")

    # Overall summary
    total_q = len(TEST_QUESTIONS)
    lines.append("")
    lines.append(f"{'=' * 60}")
    lines.append("SUMMARY:")
    lines.append(f"  Total: {total_q} questions")
    lines.append(f"  Accurate: {counts['accurate']}  Partial: {counts['partial']}  "
                 f"Hallucination: {counts['hallucination']}  Search not triggered: {counts['search_not_triggered']}  "
                 f"Errors: {counts['error']}")
    accurate_pct = (counts["accurate"] / total_q * 100) if total_q else 0
    lines.append(f"  Accuracy rate: {accurate_pct:.0f}%")
    lines.append("")

    summary = "\n".join(lines)
    return results, summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Ember-2 web search accuracy evaluation")
    parser.add_argument("--model", type=str, default="qwen3:8b", help="Model to test (default: qwen3:8b)")
    parser.add_argument("--auto-search", action="store_true", help="Enable autonomous web search for the eval session")
    parser.add_argument("--compare", action="store_true", help="Run twice: qwen3:8b then claude-haiku-4-5-20251001, produce comparison")
    args = parser.parse_args()

    # Check requirements
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set.")
        sys.exit(1)
    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("ERROR: anthropic package not installed.")
        sys.exit(1)

    log_dir = REPO_ROOT / "logs" / "eval_web_search"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Auto-search toggle
    prev_autonomous = None
    if args.auto_search:
        prev_autonomous = set_autonomous_search(True)
        print("Autonomous web search enabled for this eval session.")

    try:
        if args.compare:
            _run_comparison(log_dir)
        else:
            _run_single(args.model, log_dir)
    finally:
        if prev_autonomous is not None:
            set_autonomous_search(prev_autonomous)
            print(f"Restored web_search_autonomous to {prev_autonomous}.")


def _run_single(model: str, log_dir: Path):
    original_model = switch_model(model)
    print(f"Running web search eval (model: {model})...")
    print(f"30 questions across 5 categories. Estimated time: 15-30 minutes.\n")

    results, summary = run_eval(model)
    print(summary)

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    log_file = log_dir / f"eval_{model.replace(':', '-')}_{timestamp}.log"
    log_file.write_text(summary, encoding="utf-8")
    print(f"\nLog written to: {log_file}")

    if original_model and original_model != model:
        switch_model(original_model)
        print(f"Restored model to: {original_model}")


def _run_comparison(log_dir: Path):
    models = ["qwen3:8b", "claude-haiku-4-5-20251001"]
    all_results: dict[str, list[dict]] = {}
    all_summaries: dict[str, str] = {}

    for model in models:
        original = switch_model(model)
        print(f"\n{'=' * 60}")
        print(f"Running: {model}")
        print(f"{'=' * 60}\n")

        results, summary = run_eval(model)
        all_results[model] = results
        all_summaries[model] = summary
        print(summary)

        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        log_file = log_dir / f"eval_{model.replace(':', '-')}_{timestamp}.log"
        log_file.write_text(summary, encoding="utf-8")

        if original and original != model:
            switch_model(original)

    # Comparison table
    lines = [
        "",
        "=" * 60,
        "SIDE-BY-SIDE COMPARISON",
        "=" * 60,
        "",
        f"{'Category':25s}  {'qwen3:8b':>12s}  {'claude-haiku':>12s}",
        f"{'-' * 25}  {'-' * 12}  {'-' * 12}",
    ]

    for cat_key in ("current_events", "science_tech", "sports", "business", "culture"):
        display = CATEGORY_DISPLAY.get(cat_key, cat_key)
        scores = []
        for model in models:
            grades = [r["grade"] for r in all_results[model] if r["category"] == cat_key]
            accurate = sum(1 for g in grades if g == "accurate")
            total = len(grades)
            scores.append(f"{accurate}/{total}")
        lines.append(f"{display:25s}  {scores[0]:>12s}  {scores[1]:>12s}")

    # Totals
    totals = []
    for model in models:
        accurate = sum(1 for r in all_results[model] if r.get("grade") == "accurate")
        total = len(all_results[model])
        totals.append(f"{accurate}/{total}")
    lines.append(f"{'-' * 25}  {'-' * 12}  {'-' * 12}")
    lines.append(f"{'TOTAL':25s}  {totals[0]:>12s}  {totals[1]:>12s}")
    lines.append("")

    comparison = "\n".join(lines)
    print(comparison)

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    comp_file = log_dir / f"comparison_{timestamp}.log"
    comp_file.write_text(comparison, encoding="utf-8")
    print(f"Comparison written to: {comp_file}")


if __name__ == "__main__":
    main()
