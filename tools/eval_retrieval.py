"""
tools/eval_retrieval.py

Retrieval evaluation harness for Ember-2.

Runs 15 benchmark queries covering all query intent classes and scores
the results to catch retrieval regressions. This is evaluation only —
it does not change any retrieval logic.

Usage:
    python tools/eval_retrieval.py              # summary only
    python tools/eval_retrieval.py --verbose    # full content of top 3 results

Output:
    - stdout: pass/warn/fail per query + summary
    - logs/retrieval_eval/eval_{timestamp}.log: full log

Scoring per query (top result):
    - memory_type_present: the result has a non-empty memory_type field
    - score_above_threshold: the result's score is > 0.3
    - content_not_empty: the result's content is at least 40 characters
    - not_assistant_filler: the content doesn't look like assistant boilerplate

    PASS = all 4 criteria met
    WARN = 3 of 4 criteria met
    FAIL = fewer than 3 criteria met

Interpreting results:
    - A PASS means the top result is a valid, relevant, non-filler memory
    - A WARN means something is marginal — check the verbose output
    - A FAIL means retrieval returned garbage or nothing for that query class
    - Improving from WARN to PASS usually means tuning policy weights or
      adding better source content to the vault
"""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.context.service import ContextService  # noqa: E402


# ---------------------------------------------------------------------------
# Benchmark cases — 15 queries across 6 intent classes
# ---------------------------------------------------------------------------

@dataclass
class EvalCase:
    name: str
    query: str
    intent: str


EVAL_CASES: list[EvalCase] = [
    # Reflective (3)
    EvalCase("reflective_patterns", "What patterns have you noticed in my work?", "reflective"),
    EvalCase("reflective_how_doing", "How have I been doing lately?", "reflective"),
    EvalCase("reflective_themes", "What themes keep coming up?", "reflective"),

    # Task/work (3)
    EvalCase("work_retrieval_pipeline", "What are we building in the retrieval pipeline?", "task"),
    EvalCase("work_context_architecture", "What's the current architecture for context assembly?", "task"),
    EvalCase("work_state_layer", "What did we work on with the state layer?", "task"),

    # State/status (2)
    EvalCase("state_current_focus", "What am I focused on right now?", "status"),
    EvalCase("state_open_loops", "What are my open loops?", "status"),

    # Profile (2)
    EvalCase("profile_who_am_i", "Who am I?", "profile"),
    EvalCase("profile_work", "What do I do for work?", "profile"),

    # Timeline (2)
    EvalCase("timeline_recent", "What happened recently with Ember?", "timeline"),
    EvalCase("timeline_last_change", "What did we change last?", "timeline"),

    # Reference (3)
    EvalCase("reference_tdd_reflections", "What does the TDD say about reflections?", "reference"),
    EvalCase("reference_architecture_rules", "What are the core architectural rules?", "reference"),
    EvalCase("reference_constitution", "What is the constitution for?", "reference"),
]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

ASSISTANT_FILLER_MARKERS = (
    "as an ai",
    "i don't have personal",
    "i'm here to help",
    "how can i assist",
    "i'd be happy to",
    "let me know if",
    "feel free to",
    "is there anything else",
)


def score_result(result: dict) -> dict[str, bool]:
    """Score a single retrieval result against 4 quality criteria."""
    content = result.get("content", "")
    content_lower = content.lower().strip()

    return {
        "memory_type_present": bool(result.get("memory_type")),
        "score_above_threshold": float(result.get("score", 0)) > 0.3,
        "content_not_empty": len(content.strip()) >= 40,
        "not_assistant_filler": not any(m in content_lower for m in ASSISTANT_FILLER_MARKERS),
    }


def verdict(scores: dict[str, bool]) -> str:
    """Return PASS, WARN, or FAIL based on how many criteria are met."""
    met = sum(scores.values())
    if met == 4:
        return "PASS"
    if met == 3:
        return "WARN"
    return "FAIL"


# ---------------------------------------------------------------------------
# Context packet cleaning (strip embeddings for readability)
# ---------------------------------------------------------------------------

def clean_context_packet(packet_dict: dict) -> dict:
    for section in ["memory_items", "reflection_items", "state_items"]:
        for item in packet_dict.get(section, []):
            metadata = item.get("metadata", {})
            metadata.pop("embedding", None)
            metadata.pop("file_path", None)
    return packet_dict


# ---------------------------------------------------------------------------
# Eval runner
# ---------------------------------------------------------------------------

def run_eval(verbose: bool = False) -> tuple[list[dict], str]:
    """
    Run all benchmark cases and return (results, summary_text).
    """
    context_service = ContextService()
    results: list[dict] = []
    lines: list[str] = []
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0}

    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    lines.append(f"Ember-2 Retrieval Evaluation — {timestamp}")
    lines.append(f"{'=' * 60}")
    lines.append("")

    for case in EVAL_CASES:
        packet = context_service.build_context(case.query)
        packet_dict = clean_context_packet(asdict(packet))

        # Get top memory result for scoring
        all_items = packet_dict.get("memory_items", []) + packet_dict.get("reflection_items", [])

        if all_items:
            top = all_items[0]
            top_result = {
                "content": top.get("content", ""),
                "score": top.get("score", 0),
                "memory_type": top.get("memory_type") or top.get("item_type", ""),
            }
            scores = score_result(top_result)
        else:
            top_result = {"content": "", "score": 0, "memory_type": ""}
            scores = {k: False for k in ["memory_type_present", "score_above_threshold", "content_not_empty", "not_assistant_filler"]}

        v = verdict(scores)
        counts[v] += 1

        # Build status line
        icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[v]
        status_line = f"{icon} {v}  [{case.intent}] {case.name}"
        lines.append(status_line)
        lines.append(f"   Query: {case.query}")
        lines.append(f"   Results: {len(packet.memory_items)} memory, {len(packet.reflection_items)} reflection, {len(packet.state_items)} state")

        # Show score details on non-PASS
        if v != "PASS":
            failed = [k for k, passed in scores.items() if not passed]
            lines.append(f"   Failed criteria: {', '.join(failed)}")

        if verbose and all_items:
            lines.append(f"   Top 3 results:")
            for i, item in enumerate(all_items[:3]):
                content_preview = (item.get("content", "") or "")[:120].replace("\n", " ")
                mem_type = item.get("memory_type") or item.get("item_type", "?")
                score_val = item.get("score", 0)
                lines.append(f"     {i+1}. [{mem_type}] score={score_val:.3f} — {content_preview}")

        lines.append("")

        results.append({
            "name": case.name,
            "intent": case.intent,
            "query": case.query,
            "verdict": v,
            "scores": scores,
            "memory_count": len(packet.memory_items),
            "reflection_count": len(packet.reflection_items),
            "state_count": len(packet.state_items),
        })

    # Summary
    total = len(EVAL_CASES)
    lines.append(f"{'=' * 60}")
    lines.append(f"SUMMARY: {counts['PASS']} passed, {counts['WARN']} warned, {counts['FAIL']} failed out of {total}")
    lines.append("")

    if counts["FAIL"] > 0:
        lines.append("Failed queries need attention — check vault content and retrieval policy weights.")
    elif counts["WARN"] > 0:
        lines.append("All queries returned usable results, but some are marginal. Run with --verbose to inspect.")
    else:
        lines.append("All queries returning high-quality results. Retrieval is healthy.")

    summary = "\n".join(lines)
    return results, summary


def main():
    verbose = "--verbose" in sys.argv

    print("Running retrieval evaluation...\n")
    results, summary = run_eval(verbose=verbose)

    # Print to stdout
    print(summary)

    # Write to log file
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    log_dir = REPO_ROOT / "logs" / "retrieval_eval"
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / f"eval_{timestamp}.log"
    log_file.write_text(summary, encoding="utf-8")
    print(f"\nLog written to: {log_file}")

    # Also write latest.json for programmatic access
    import json
    json_file = log_dir / "latest.json"
    json_file.write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
