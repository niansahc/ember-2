"""
tools/traffic_window.py

Drive a designed traffic window at a running API so the guard counters
have something to count.

The counters answer "which guards are never true on real data", and that
question needs traffic. There is no recorded natural traffic to replay and
no way to synthesise a representative one, so the population here is
DESIGNED: a query set written to walk every classifier branch and every
retrieval path on purpose. That is a different thing from a sample, and it
biases the result in a specific direction -- see the module docstring of
tools/guard_counter_sites.py for the accounting, and the report for what
the bias costs. In short: a guard that fires here is confirmed reachable,
and a guard that never fires is only a candidate for dead configuration
until the query set is shown to have had a way to reach it.

Two modes:

    python tools/traffic_window.py --classify   # no API, no writes
    python tools/traffic_window.py --run        # real turns at the API

--classify routes every query through classify_query and prints which
policy each one actually got, plus which policies the set covers. Intent
is not routing: #230 lost a policy to exactly this gap, where a query
written for one policy was claimed by an earlier branch of the cascade.
Check coverage here before spending a window on it.

--run refuses to drive the personal vault. A designed window writes
retrieval stats and conversation records, and on the personal vault that
would promote real records and deposit synthetic ones for the sake of a
measurement. Point the API at a test vault first, or swap to one (POST
/v1/developer/vault/swap). The driver compares the API's active vault path
against PRIVATE_VAULT_PATH as written in .env, not against the label and
not against its own environment -- the check has to survive being run by a
process whose own environment was overridden.

Prints queries, policy names and counts. Never prints a response.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_BASE_URL = "http://127.0.0.1:8000"

# Every query is synthetic and generic. None of it is drawn from a vault.
#
# `targets` records what the query was written to exercise, so that a
# guard which never fires can be checked against the set: if nothing
# targeted it, the finding is a gap in the population, not a dead guard.
QUERY_SET: tuple[dict, ...] = (
    # -- policy cascade -----------------------------------------------
    {"query": "what is the current status of my open tasks",
     "intent": "task_status", "targets": ["policy:task_status", "type_gate"]},
    {"query": "what am i focused on right now",
     "intent": "status_state", "targets": ["policy:status_state", "state_boost"]},
    {"query": "what patterns have shown up in how i work",
     "intent": "reflective", "targets": ["policy:reflective", "selection.mode=diversity"]},
    {"query": "recall what was decided about the embedding model",
     "intent": "factual_recall", "targets": ["policy:factual_recall", "exact_match"]},
    {"query": "what was i working on lately",
     "intent": "recent_activity", "targets": ["policy:recent_activity", "recency"]},
    {"query": "what came up recently",
     "intent": "recent", "targets": ["policy:recent", "recency"]},
    {"query": "the ingestion pipeline i am building",
     "intent": "activity", "targets": ["policy:activity"]},
    {"query": "tell me about the architecture",
     "intent": "default", "targets": ["policy:default", "relevance_gate"]},
    {"query": "what is the current price of a barrel of brent crude",
     "intent": "web_search", "targets": ["policy:web_search", "web_quarantine"]},
    {"query": "search the web",
     "intent": "clarification", "targets": ["policy:clarification"]},

    # -- relational and identity --------------------------------------
    {"query": "what do i tend to say about my work",
     "intent": "any", "targets": ["ranker.authorship.relational_query",
                                  "zero_hit_signal.relational_query"]},
    {"query": "how has my health come up before",
     "intent": "any", "targets": ["ranker.authorship.relational_query"]},
    {"query": "what matters to my partner",
     "intent": "any", "targets": ["ranker.authorship.relational_query",
                                  "ranker.authorship.branch=third_party"]},
    {"query": "who am i",
     "intent": "any", "targets": ["profile.identity_query", "type_gate.profile_bypass",
                                  "reserved_slots.profile_present"]},
    {"query": "what should you know about me",
     "intent": "any", "targets": ["profile.identity_query", "reserved_slots"]},

    # -- stores -------------------------------------------------------
    {"query": "summarise the imported reference material on retrieval",
     "intent": "any", "targets": ["semantic_search.*.ingested",
                                  "vector_index.min_score_floor.json",
                                  "diversity.group_yielded.ingested"]},
    {"query": "what does the imported documentation say about chunking",
     "intent": "any", "targets": ["semantic_search.*.ingested",
                                  "semantic_search.should_exclude_result.json_typed"]},
    {"query": "find the imported notes about evaluation methodology",
     "intent": "any", "targets": ["vector_index.min_score_floor.json",
                                  "semantic_search.should_exclude_result.json_all_types"]},

    # -- web ----------------------------------------------------------
    {"query": "search the web for the latest release of sqlite",
     "intent": "web_search", "targets": ["web_quarantine.ai_doc_detected",
                                         "policy:web_search"]},
    # Results that name an AI system and carry a doc marker, from a query
    # that is not itself an inquiry about one -- the escape hatch returns
    # early, so ai_doc_detected is only reachable when it does not fire.
    {"query": "search the web for the ollama model card and context window",
     "intent": "web_search", "targets": ["web_quarantine.ai_doc_detected"]},
    # The escape hatch: an explicit search whose subject IS an AI system.
    {"query": "search the web, what is anthropic",
     "intent": "web_search", "targets": ["web_quarantine.ai_inquiry_escape_hatch"]},

    # -- retrieval edges ----------------------------------------------
    {"query": "zqxjw plerbon fastigate murnly",
     "intent": "any", "targets": ["min_score_floor", "zero_hit_signal.all_non_profile_zeroed",
                                  "diversity.round_made_no_progress"]},
    {"query": "a",
     "intent": "any", "targets": ["low_value_filter.under_40_chars", "min_score_floor"]},
    {"query": "what did i decide about the retrieval scoring composition order",
     "intent": "any", "targets": ["echo_filter.query_verbatim_in_content",
                                  "retriever.exclude.query_verbatim_in_content"]},
    {"query": "show me everything about everything",
     "intent": "any", "targets": ["diversity.fell_short_of_limit",
                                  "diversity.backfill_exhausted"]},
    {"query": "list the open loops and what is blocked",
     "intent": "any", "targets": ["type_gate.not_eligible_type",
                                  "type_gate.suppressed_type"]},
    {"query": "what changed in the last week",
     "intent": "any", "targets": ["ranker.recency.bucket=d7",
                                  "ranker.decay.bucket.ephemeral"]},
    {"query": "what was decided a long time ago that still applies",
     "intent": "any", "targets": ["ranker.recency.bucket=older",
                                  "ranker.decay.bucket.default=older"]},
    {"query": "summarise the weekly synthesis",
     "intent": "any", "targets": ["ranker.decay.family=reflection",
                                  "ranker.reflection.under_30_chars"]},
    {"query": "what tasks are blocked and what is the next action on each",
     "intent": "any", "targets": ["ranker.policy.prefer_active_work_fired",
                                  "selection.mode=score_order"]},
    {"query": "is the vector index rebuilt from canonical records",
     "intent": "any", "targets": ["ranker.policy.exact_match_question",
                                  "ranker.length.over_1200"]},
    {"query": "what happened in the session where the index was rebuilt",
     "intent": "any", "targets": ["selection.dedup.duplicate_content",
                                  "retriever.dedup.duplicate_content"]},
)


def classify_set() -> list[tuple[dict, str]]:
    from src.context.policies import classify_query

    return [(entry, classify_query(entry["query"]).name) for entry in QUERY_SET]


def cmd_classify() -> int:
    from src.context.policies import _matches_relational_query

    rows = classify_set()
    print(f"  queries: {len(rows)}")
    print()
    print(f"    {'assigned':16} {'intended':16} {'rel':4} query")
    for entry, assigned in rows:
        intended = entry["intent"]
        mark = " " if intended in {assigned, "any"} else "!"
        relational = "yes" if _matches_relational_query(entry["query"]) else "-"
        print(f"  {mark} {assigned:16} {intended:16} {relational:4} {entry['query']}")

    assigned = {name for _, name in rows}
    expected = {
        "task_status", "status_state", "reflective", "factual_recall",
        "recent_activity", "recent", "activity", "default", "web_search",
        "clarification",
    }
    print()
    print(f"  policies covered: {len(assigned & expected)}/{len(expected)}")
    missing = sorted(expected - assigned)
    if missing:
        print(f"  MISSING: {', '.join(missing)}")
        return 1
    return 0


def _post(url: str, payload: dict, api_key: str | None, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    if api_key:
        request.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _get(url: str, api_key: str | None, timeout: float = 15.0) -> dict:
    request = urllib.request.Request(url, method="GET")
    if api_key:
        request.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _personal_vault_from_env_file() -> Path | None:
    """The personal vault path as .env declares it.

    Read from the file rather than from os.environ on purpose: this
    process may itself have been started with PRIVATE_VAULT_PATH pointed
    somewhere else, and then the environment would agree with whatever it
    was overridden to. The file is the stable statement of which vault is
    the real one.
    """
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("PRIVATE_VAULT_PATH="):
            continue
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        return Path(value).resolve() if value else None
    return None


def cmd_run(base_url: str, model: str, timeout: float) -> int:
    from src.core.config import get_ember_api_key

    api_key = get_ember_api_key() or None

    status = _get(f"{base_url}/v1/developer/vault/status", api_key)
    label = (status.get("label") or "default").lower()
    active = Path(status["active_vault"]).resolve()
    personal = _personal_vault_from_env_file()
    print(f"  active vault label: {label}")
    if personal is not None and active == personal:
        print("  REFUSED: the driver will not run a designed window against the "
              "personal vault. Point the API at a test vault first.")
        return 2
    if personal is None:
        print("  REFUSED: could not read PRIVATE_VAULT_PATH from .env, so the "
              "personal vault cannot be ruled out.")
        return 2

    rows = classify_set()
    print(f"  turns: {len(rows)}")
    print()
    failures = 0
    started = time.perf_counter()
    for index, (entry, assigned) in enumerate(rows, start=1):
        turn_started = time.perf_counter()
        try:
            _post(
                f"{base_url}/v1/chat/completions",
                {
                    "model": model,
                    "messages": [{"role": "user", "content": entry["query"]}],
                    "stream": False,
                },
                api_key,
                timeout,
            )
            outcome = "ok"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            outcome = f"FAILED {type(exc).__name__}"
            failures += 1
        elapsed = time.perf_counter() - turn_started
        # No response content: a designed query still returns vault-grounded
        # text, and that text is vault content.
        print(f"  {index:3}/{len(rows)}  {assigned:16} {elapsed:6.1f}s  {outcome}")

    print()
    print(f"  window: {len(rows)} turns, {failures} failed, "
          f"{time.perf_counter() - started:.0f}s total")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--classify", action="store_true",
                        help="route the query set locally, no API calls")
    parser.add_argument("--run", action="store_true",
                        help="drive the window at a running API")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default="ember",
                        help="model name sent in the request body")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    if args.classify:
        return cmd_classify()
    if args.run:
        return cmd_run(args.base_url, args.model, args.timeout)
    parser.error("choose --classify or --run")


if __name__ == "__main__":
    raise SystemExit(main())
