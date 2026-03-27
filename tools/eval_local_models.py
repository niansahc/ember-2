"""
tools/eval_local_models.py

Automated local model comparison evaluation.

Runs the conversation quality eval harness (tools/eval_conversations.py)
against multiple local Ollama models and produces a comparison report.

Requirements:
    - Ember API running at http://localhost:8000
    - ANTHROPIC_API_KEY set in environment
    - pip install anthropic
    - Models installed via ollama pull

Usage:
    python tools/eval_local_models.py
    python tools/eval_local_models.py --verbose

Estimated time: 30-60 minutes (18 tests per model × N models)
Estimated cost: $0.50-2.00 in Claude API calls
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import httpx
from tools.eval_conversations import (
    TEST_CASES,
    send_to_ember,
    evaluate_with_claude,
    get_ember_api_key,
)


# ---------------------------------------------------------------------------
# Models to evaluate
# ---------------------------------------------------------------------------

MODELS_TO_TEST = [
    "qwen2.5:14b",    # Current default — baseline
    "qwen3:8b",
    "mistral:7b",
    "phi4:14b",
    "gemma3:12b",
    "llama3.1:8b",
]

ORIGINAL_MODEL = "qwen2.5:14b"

CATEGORIES = [
    "Preference expression",
    "Self-attribution",
    "Tone and presence",
    "State awareness",
    "Memory grounding",
    "Constitutional behavior",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_installed_models() -> set[str]:
    """Get set of installed Ollama model names."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=10,
        )
        lines = result.stdout.strip().split("\n")[1:]  # skip header
        return {line.split()[0] for line in lines if line.strip()}
    except Exception:
        return set()


def switch_model(model: str) -> bool:
    """Switch Ember's active model via POST /model."""
    try:
        api_key = get_ember_api_key()
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        resp = httpx.post(
            "http://localhost:8000/model",
            json={"model": model},
            headers=headers,
            timeout=30.0,
        )
        return resp.status_code == 200
    except Exception as exc:
        print(f"  Failed to switch model: {exc}")
        return False


def get_current_model() -> str:
    """Get the currently active model."""
    try:
        api_key = get_ember_api_key()
        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        resp = httpx.get(
            "http://localhost:8000/model",
            headers=headers,
            timeout=10.0,
        )
        return resp.json().get("model", "unknown")
    except Exception:
        return "unknown"


def run_eval_for_model(model: str, verbose: bool = False) -> dict:
    """
    Run the full 18-question eval for a single model.
    Returns {model, overall, categories: {name: score}, results: [...]}
    """
    category_scores: dict[str, list[int]] = {cat: [] for cat in CATEGORIES}
    counts = {"pass": 0, "warn": 0, "fail": 0, "error": 0}
    results = []

    for i, case in enumerate(TEST_CASES, 1):
        cat = case["category"]
        msg = case["message"]
        criteria = case["criteria"]

        print(f"    [{i:02d}/{len(TEST_CASES)}] {cat}: \"{msg[:50]}...\"", end="", flush=True)

        # Send to Ember
        ember_result = send_to_ember(msg)
        if not ember_result["ok"]:
            print(f" ❌ Ember error")
            counts["error"] += 1
            results.append({"category": cat, "message": msg, "result": "error"})
            continue

        response = ember_result["response"]

        # Evaluate with Claude
        eval_result = evaluate_with_claude(cat, msg, response, criteria)
        if not eval_result["ok"]:
            print(f" ❌ Claude error")
            counts["error"] += 1
            results.append({"category": cat, "message": msg, "result": "error"})
            continue

        result = eval_result["result"]
        score = eval_result["score"]
        counts[result] = counts.get(result, 0) + 1
        category_scores[cat].append(score)

        icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(result, "❓")
        print(f" {icon} {score}/10")

        results.append({
            "category": cat,
            "message": msg,
            "result": result,
            "score": score,
            "notes": eval_result.get("notes", ""),
            "response": response if verbose else response[:200],
        })

    # Calculate averages
    cat_avgs = {}
    all_scores = []
    for cat, scores in category_scores.items():
        if scores:
            avg = sum(scores) / len(scores)
            cat_avgs[cat] = round(avg, 1)
            all_scores.extend(scores)
        else:
            cat_avgs[cat] = 0.0

    overall = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0.0

    return {
        "model": model,
        "overall": overall,
        "categories": cat_avgs,
        "counts": counts,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    verbose = "--verbose" in sys.argv

    # Check requirements
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set in environment.")
        sys.exit(1)

    try:
        import anthropic  # noqa: F401
    except ImportError:
        print("ERROR: anthropic package not installed. pip install anthropic")
        sys.exit(1)

    # Warning
    print("=" * 60)
    print("LOCAL MODEL COMPARISON EVAL")
    print("=" * 60)
    print(f"Models to test: {len(MODELS_TO_TEST)}")
    print(f"Tests per model: {len(TEST_CASES)}")
    print(f"Total tests: {len(MODELS_TO_TEST) * len(TEST_CASES)}")
    print()
    print("This will take approximately 30-60 minutes and will make")
    print("API calls to Claude for evaluation.")
    print("Estimated cost: $0.50-2.00.")
    print()
    input("Press Enter to continue or Ctrl+C to cancel...")
    print()

    # Check which models are installed
    installed = get_installed_models()
    print(f"Installed models: {sorted(installed)}")
    print()

    # Record starting model
    original = get_current_model()
    print(f"Current model: {original}")
    print()

    # Run evals
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    all_results: list[dict] = []
    lines: list[str] = []

    lines.append(f"Local Model Comparison Eval — {timestamp}")
    lines.append(f"Evaluator: claude-sonnet-4-20250514")
    lines.append(f"{'=' * 70}")
    lines.append("")

    for model in MODELS_TO_TEST:
        print(f"{'─' * 60}")
        print(f"MODEL: {model}")
        print(f"{'─' * 60}")

        # Check if installed
        if model not in installed:
            print(f"  ⏭️  Not installed, skipping.")
            lines.append(f"{model:20s} | SKIPPED — not installed")
            all_results.append({"model": model, "overall": None, "skipped": True})
            print()
            continue

        # Switch model
        print(f"  Switching to {model}...")
        if not switch_model(model):
            print(f"  ❌ Failed to switch model, skipping.")
            lines.append(f"{model:20s} | SKIPPED — switch failed")
            all_results.append({"model": model, "overall": None, "skipped": True})
            print()
            continue

        # Wait for model to load
        print(f"  Waiting 3 seconds for model to load...")
        time.sleep(3)

        # Run eval
        print(f"  Running 18-question eval...")
        result = run_eval_for_model(model, verbose=verbose)
        all_results.append(result)

        print(f"\n  Overall: {result['overall']}/10")
        print(f"  Passed: {result['counts']['pass']}  Warned: {result['counts']['warn']}  Failed: {result['counts']['fail']}  Errors: {result['counts']['error']}")
        print()

    # Restore original model
    print(f"Restoring original model: {ORIGINAL_MODEL}...")
    switch_model(ORIGINAL_MODEL)
    print()

    # Build comparison table
    lines.append("")
    header = f"{'Model':20s} | {'Overall':>7} | {'Prefer':>6} | {'Const':>5} | {'Memory':>6} | {'Self-A':>6} | {'State':>5} | {'Tone':>4}"
    separator = "─" * len(header)
    lines.append(header)
    lines.append(separator)

    print("=" * 70)
    print("COMPARISON TABLE")
    print("=" * 70)
    print(header)
    print(separator)

    best_model = None
    best_score = -1

    for r in all_results:
        if r.get("skipped"):
            row = f"{r['model']:20s} | {'SKIP':>7} |   —    |  —    |   —    |   —    |  —    |  —"
        else:
            cats = r["categories"]
            row = (
                f"{r['model']:20s} | "
                f"{r['overall']:>5.1f}/10 | "
                f"{cats.get('Preference expression', 0):>5.1f} | "
                f"{cats.get('Constitutional behavior', 0):>5.1f} | "
                f"{cats.get('Memory grounding', 0):>5.1f} | "
                f"{cats.get('Self-attribution', 0):>5.1f} | "
                f"{cats.get('State awareness', 0):>5.1f} | "
                f"{cats.get('Tone and presence', 0):>4.1f}"
            )
            if r["overall"] > best_score:
                best_score = r["overall"]
                best_model = r["model"]

        lines.append(row)
        print(row)

    lines.append(separator)
    print(separator)

    if best_model:
        winner_line = f"\n🏆 Winner: {best_model} ({best_score}/10)"
        lines.append(winner_line)
        print(winner_line)

    # Write log
    full_log = "\n".join(lines)
    log_dir = REPO_ROOT / "logs" / "model_eval"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"eval_{timestamp}.log"
    log_file.write_text(full_log, encoding="utf-8")
    print(f"\nLog written to: {log_file}")

    # Write JSON
    json_file = log_dir / "latest.json"
    json_file.write_text(
        json.dumps(all_results, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"JSON written to: {json_file}")


if __name__ == "__main__":
    main()
