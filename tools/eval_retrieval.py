"""
tools/eval_retrieval.py

Retrieval evaluation harness for Ember-2.

Runs 15 benchmark queries against the real vault covering all query
intent classes, plus 5 contextual integrity cases using synthetic
fixtures to test type gating behavior.

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


# ---------------------------------------------------------------------------
# Contextual integrity cases — 5 cases with synthetic fixtures
# ---------------------------------------------------------------------------
# These test type gating behavior (ADR-018) using controlled synthetic
# records. They do not touch the real vault. Each case provides a query,
# a set of synthetic retrieval results, and an expected outcome:
#   "suppress" = the tagged record should NOT appear in final results
#   "surface"  = the tagged record SHOULD appear in final results

@dataclass
class IntegrityCase:
    name: str
    query: str
    intent: str  # the intent class the query should match
    tagged_record: dict  # the record we're testing for
    other_records: list  # normal records that should always surface
    expect: str  # "suppress" or "surface"


def _make_item(content: str, memory_type: str, score: float = 0.6, **extra_meta) -> dict:
    """Build a minimal ContextItem-compatible dict for synthetic fixtures."""
    return {
        "id": f"synth-{hash(content) % 10000}",
        "content": content,
        "source": memory_type,
        "item_type": memory_type,
        "memory_type": memory_type,
        "score": score,
        "metadata": extra_meta,
    }


INTEGRITY_CASES: list[IntegrityCase] = [
    # 1. Health-in-work context: work query, health record present → suppress
    IntegrityCase(
        name="health_in_work_context",
        query="What's blocking my project right now?",
        intent="task",
        tagged_record=_make_item(
            "User manages a chronic joint condition that limits long sitting sessions and requires regular breaks throughout the day.",
            "journal", score=0.55,
        ),
        other_records=[
            _make_item("The retrieval pipeline refactor is blocked on the index migration — can't merge until SQLite store is stable.", "conversation", score=0.72),
            _make_item("Need to resolve the dependency conflict between httpx and the new test harness before the next release.", "conversation", score=0.65),
        ],
        expect="suppress",
    ),
    # 2. Personal-in-professional context: work deliverable query, journal entry → suppress
    IntegrityCase(
        name="personal_in_professional",
        query="What's the status of the API refactor?",
        intent="task",
        tagged_record=_make_item(
            "Had a long conversation about feeling disconnected from close friends after the move. Missing the spontaneous drop-ins that used to happen.",
            "journal", score=0.45,
        ),
        other_records=[
            _make_item("API refactor: split main.py into route modules. Auth middleware extracted. Rate limiter consolidated.", "conversation", score=0.78),
        ],
        expect="suppress",
    ),
    # 3. Cross-domain leakage: technical query, emotional reflection → suppress
    IntegrityCase(
        name="cross_domain_leakage",
        query="How does the context ranker score memories?",
        intent="reference",
        tagged_record=_make_item(
            "This week was emotionally heavy. The combination of work pressure and family obligations left little room for rest. Noticed a pattern of pushing through without checking in.",
            "reflection", score=0.40,
        ),
        other_records=[
            _make_item("ContextRanker applies policy weights, recency boost, role scoring, and diversity selection. Assistant content penalized at -0.25.", "ingested", score=0.82),
        ],
        expect="suppress",
    ),
    # 4. Appropriate health surfacing: health query, health record → surface
    IntegrityCase(
        name="appropriate_health_surface",
        query="I'm exhausted and can't focus — what do I usually do when this happens?",
        intent="reflective",
        tagged_record=_make_item(
            "User manages a chronic joint condition that limits long sitting sessions and requires regular breaks throughout the day.",
            "journal", score=0.65,
        ),
        other_records=[
            _make_item("Noticed a recurring pattern: energy drops after two hours of deep focus. Walking break restores about 80% of capacity.", "reflection", score=0.60),
        ],
        expect="surface",
    ),
    # 5. Appropriate personal surfacing: reflective query, personal records → surface
    IntegrityCase(
        name="appropriate_personal_surface",
        query="What have I been struggling with lately?",
        intent="reflective",
        tagged_record=_make_item(
            "Had a long conversation about feeling disconnected from close friends after the move. Missing the spontaneous drop-ins that used to happen.",
            "journal", score=0.58,
        ),
        other_records=[
            _make_item("Work has been steady but the creative projects are stalling. Three ideas started, none past the outline stage.", "conversation", score=0.62),
        ],
        expect="surface",
    ),
]


def run_integrity_eval() -> tuple[list[dict], str]:
    """
    Run contextual integrity cases with synthetic fixtures.

    Each case patches the retrieval layer to return controlled records,
    then checks whether the tagged record was correctly surfaced or
    suppressed by the type gating and policy weighting pipeline.
    """
    from unittest.mock import patch, MagicMock
    from src.context.models import ContextItem

    def _dict_to_item(d: dict) -> ContextItem:
        return ContextItem(
            id=d["id"],
            content=d["content"],
            source=d["source"],
            item_type=d["item_type"],
            memory_type=d["memory_type"],
            score=d["score"],
            metadata=d.get("metadata", {}),
        )

    results: list[dict] = []
    lines: list[str] = []
    counts = {"PASS": 0, "FAIL": 0}

    lines.append("")
    lines.append(f"{'=' * 60}")
    lines.append("Contextual Integrity Cases (synthetic fixtures)")
    lines.append(f"{'=' * 60}")
    lines.append("")

    for case in INTEGRITY_CASES:
        all_synthetic = [case.tagged_record] + case.other_records
        synthetic_items = [_dict_to_item(d) for d in all_synthetic]
        tagged_content = case.tagged_record["content"]

        # Patch the retriever to return our synthetic items
        context_service = ContextService()

        def _mock_retrieve(query, *args, **kwargs):
            # Return: state_items, task_items, memory_items, reflection_items, query_embedding
            mem = [i for i in synthetic_items if i.memory_type != "reflection"]
            ref = [i for i in synthetic_items if i.memory_type == "reflection"]
            return [], [], mem, ref, None

        with patch.object(context_service.retriever, "retrieve", side_effect=_mock_retrieve):
            packet = context_service.build_context(case.query)

        # Check if tagged record content appears in the final packet
        all_content = [item.content for item in packet.memory_items + packet.reflection_items]
        tagged_present = any(tagged_content in c for c in all_content)

        if case.expect == "suppress":
            passed = not tagged_present
        else:  # "surface"
            passed = tagged_present

        v = "PASS" if passed else "FAIL"
        counts[v] += 1

        icon = "✅" if passed else "❌"
        lines.append(f"{icon} {v}  [integrity:{case.expect}] {case.name}")
        lines.append(f"   Query: {case.query}")
        lines.append(f"   Tagged record type: {case.tagged_record['memory_type']}")
        lines.append(f"   Expected: {case.expect}  Actual: {'present' if tagged_present else 'absent'}")
        if not passed:
            lines.append(f"   ** MISMATCH — tagged record was {'present' if tagged_present else 'absent'} but expected {case.expect}")
        lines.append("")

        results.append({
            "name": case.name,
            "intent": f"integrity:{case.expect}",
            "query": case.query,
            "verdict": v,
            "tagged_type": case.tagged_record["memory_type"],
            "expect": case.expect,
            "actual": "present" if tagged_present else "absent",
        })

    lines.append(f"INTEGRITY SUMMARY: {counts['PASS']} passed, {counts['FAIL']} failed out of {len(INTEGRITY_CASES)}")
    summary = "\n".join(lines)
    return results, summary


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    verbose = "--verbose" in sys.argv

    print("Running retrieval evaluation...\n")
    results, summary = run_eval(verbose=verbose)

    # Run contextual integrity cases
    integrity_results, integrity_summary = run_integrity_eval()
    summary += "\n" + integrity_summary
    results += integrity_results

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
