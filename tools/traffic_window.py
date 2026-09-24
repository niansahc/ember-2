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

Three modes:

    python tools/traffic_window.py --classify      # no API, no writes
    python tools/traffic_window.py --run           # real turns at the API
    python tools/traffic_window.py --run-readonly  # in-process, no writes

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

--run-readonly is how a corpus that must not be written to gets measured.
It calls build_context directly with read_only=True inside a suppressed-
stats scope, so there is no generation, no conversation record, no
extracted state and no retrieval stat write -- and build_context is the
whole instrumented surface, so the guards see the same turn either way. It
snapshots the memory store, rehearses restoring it, and digests the vault
on both sides of the window rather than trusting the flags. Counting under
suppression needs the measurement window declared explicitly
(EMBER_GUARD_COUNTER_WINDOW=1), and then the counter database has to live
outside the repository.

Prints queries, policy names and counts. Never prints a response, and
never prints the resolved project id -- that one is vault-derived.
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

    # -- the three gaps #234 found in this set ------------------------
    # 1. No turn carried a project, so the ADR-007 boost was evaluated
    #    against an absent id and its match arm was never reached. The id
    #    is resolved at run time, never written down.
    {"query": "where did this project get to", "project": True,
     "intent": "any", "targets": ["ranker.project.active",
                                  "ranker.project.match"]},
    # 2. prefer_exact_matches was active fifteen times without a single
    #    question-form query behind it, so the question predicate never
    #    had a chance.
    {"query": "when did i decide to use sqlite for the vector store?",
     "intent": "factual_recall", "targets": ["ranker.policy.exact_match_question",
                                             "ranker.policy.prefer_exact_matches_enabled"]},
    # 3. The zero-hit signal needs a relational query. Two ways in: every
    #    non-profile candidate scored to zero (the third-party multiplier
    #    is the only thing that does that), or no non-profile candidate
    #    surviving the relevance gate at all.
    {"query": "what has my partner said about work lately",
     "intent": "any", "targets": ["zero_hit_signal.all_non_profile_zeroed",
                                  "ranker.authorship.branch=third_party"]},
    {"query": "my hamster's firmware update schedule",
     "intent": "any", "targets": ["zero_hit_signal.profile_only",
                                  "relevance_gate.suppressed_non_profile"]},
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


# ---------------------------------------------------------------------------
# Read-only window over a corpus that must not be written to
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _vault_digest(vault: Path) -> dict:
    """A digest that notices a write anywhere in the vault.

    Every store gets a content hash. Records get name, size and mtime
    rather than content, because hashing a corpus of that size on both
    sides of the window costs minutes and a new or rewritten record
    changes the manifest anyway. An append-only store is exactly the case
    where a manifest is sufficient.
    """
    import hashlib

    stores = {}
    embeddings = vault / "embeddings"
    if embeddings.is_dir():
        for path in sorted(embeddings.glob("*.db")):
            stores[path.name] = _sha256(path)

    manifest = hashlib.sha256()
    files = 0
    for path in sorted(vault.rglob("*")):
        if not path.is_file() or path.suffix == ".db":
            continue
        stat = path.stat()
        manifest.update(
            f"{path.relative_to(vault).as_posix()}|{stat.st_size}|"
            f"{stat.st_mtime_ns}\n".encode("utf-8")
        )
        files += 1
    return {"stores": stores, "files": files, "manifest": manifest.hexdigest()}


def _snapshot_store(source: Path, destination: Path) -> dict:
    """Copy a store and prove the copy is the original.

    sqlite3's own backup API rather than a file copy: a live WAL database
    copied byte by byte can land mid-transaction, and a snapshot that
    cannot be restored is worse than none because it is believed.
    """
    import sqlite3

    destination.parent.mkdir(parents=True, exist_ok=True)
    reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    writer = sqlite3.connect(str(destination))
    try:
        reader.backup(writer)
    finally:
        writer.close()
        reader.close()

    checks = sqlite3.connect(f"file:{destination}?mode=ro", uri=True)
    try:
        integrity = checks.execute("PRAGMA integrity_check").fetchone()[0]
        tables = [
            row[0] for row in checks.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        ]
        rows = {
            table: checks.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tables
        }
    finally:
        checks.close()

    return {
        "source_sha256": _sha256(source),
        "snapshot_sha256": _sha256(destination),
        "integrity": integrity,
        "rows": rows,
    }


def _discover_project_id(vault: Path) -> str | None:
    """The most-used project id in the corpus, or None.

    Resolved at run time and held in memory. It is vault-derived, so it is
    never printed, logged or written to an artefact -- the window only
    needs to pass it in, not to know what it says.
    """
    import json
    from collections import Counter

    counts: Counter[str] = Counter()
    memory = vault / "memory"
    if not memory.is_dir():
        return None
    for path in memory.rglob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 -- a malformed record is not our business
            continue
        if not isinstance(record, dict):
            continue
        project_id = (record.get("metadata") or {}).get("project_id")
        if isinstance(project_id, str) and project_id:
            counts[project_id] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def cmd_run_readonly(artefacts: Path, project_id: str | None) -> int:
    """Drive the set in-process, read-only, against the configured vault.

    No API and no generation on purpose. The instrumented surface is
    build_context, so a turn here reaches every guard a served turn
    reaches, and it reaches none of the writes -- no conversation record,
    no extracted state, no retrieval stats. That is what makes it safe to
    point at real memory.

    Three belts: read_only=True stops the stats write inside
    build_context, retrieval_stats_disabled() stops it at the store for
    anything that does not go through build_context, and the vault is
    digested on both sides to check rather than trust.
    """
    import os

    from src.core.config import get_private_vault_path
    from src.observability import guard_counters as counters
    from src.retrieval.retrieval_stats import retrieval_stats_disabled

    if not counters.window_override_active():
        print(f"  REFUSED: set {counters.ENV_WINDOW}=1 to declare a measurement "
              "window. Without it a read-only turn records nothing.")
        return 2

    database = counters.database_path()
    try:
        counters.assert_outside_repo(database)
    except ValueError as exc:
        print(f"  REFUSED: {exc}")
        return 2

    vault = get_private_vault_path().resolve()
    artefacts.mkdir(parents=True, exist_ok=True)
    try:
        counters.assert_outside_repo(artefacts)
    except ValueError as exc:
        print(f"  REFUSED: {exc}")
        return 2

    print(f"  counter database : {database}")
    print(f"  artefacts        : {artefacts}")

    memory_db = vault / "embeddings" / "memory.db"
    snapshot = artefacts / "memory.db.snapshot"
    if not memory_db.exists():
        print("  REFUSED: no memory.db to snapshot.")
        return 2

    details = _snapshot_store(memory_db, snapshot)
    print(f"  snapshot         : integrity={details['integrity']} "
          f"tables={len(details['rows'])}")
    if details["integrity"] != "ok":
        print("  REFUSED: snapshot failed its integrity check.")
        return 2

    # Rehearse the restore rather than assert it. A snapshot is only worth
    # taking if putting it back produces the original, and the cheap way
    # to know that is to put it back somewhere harmless and compare.
    rehearsal = artefacts / "memory.db.restore-rehearsal"
    rehearsed = _snapshot_store(snapshot, rehearsal)
    restorable = (
        rehearsed["integrity"] == "ok"
        and rehearsed["rows"] == details["rows"]
        and rehearsed["snapshot_sha256"] == details["snapshot_sha256"]
    )
    print(f"  restore rehearsal: {'VERIFIED' if restorable else 'FAILED'} "
          f"(integrity={rehearsed['integrity']}, rows match="
          f"{rehearsed['rows'] == details['rows']})")
    if not restorable:
        print("  REFUSED: the snapshot does not restore to itself.")
        return 2
    rehearsal.unlink(missing_ok=True)

    before = _vault_digest(vault)
    print(f"  digest before    : {before['manifest'][:16]} "
          f"({before['files']} files, {len(before['stores'])} stores)")

    if project_id is None:
        project_id = _discover_project_id(vault)
    print(f"  project id       : {'resolved' if project_id else 'none found'}")

    from src.context.service import ContextService

    service = ContextService()
    rows = classify_set()
    print(f"  turns: {len(rows)}")
    print()

    failures = 0
    started = time.perf_counter()
    with retrieval_stats_disabled():
        for index, (entry, assigned) in enumerate(rows, start=1):
            turn_started = time.perf_counter()
            try:
                packet = service.build_context(
                    entry["query"],
                    project_id=project_id if entry.get("project") else None,
                    read_only=True,
                )
                outcome = f"ok delivered={len(packet.memory_items)}"
            except Exception as exc:  # noqa: BLE001
                outcome = f"FAILED {type(exc).__name__}"
                failures += 1
            print(f"  {index:3}/{len(rows)}  {assigned:16} "
                  f"{time.perf_counter() - turn_started:6.1f}s  {outcome}")

    print()
    print(f"  window: {len(rows)} turns, {failures} failed, "
          f"{time.perf_counter() - started:.0f}s total")

    after = _vault_digest(vault)
    unchanged = (
        after["manifest"] == before["manifest"]
        and after["stores"] == before["stores"]
        and after["files"] == before["files"]
    )
    print(f"  digest after     : {after['manifest'][:16]} "
          f"({after['files']} files, {len(after['stores'])} stores)")
    print(f"  vault unchanged  : {unchanged}")
    if not unchanged:
        changed = sorted(
            name for name, digest in after["stores"].items()
            if before["stores"].get(name) != digest
        )
        print(f"  CHANGED stores   : {', '.join(changed) or 'none'}")
        print(f"  CHANGED records  : manifest differs "
              f"({after['files'] - before['files']:+d} files)")
        print("  The snapshot above restores memory.db. Nothing is restored "
              "automatically: an unexpected write needs looking at before it "
              "is overwritten.")
        return 3

    return 1 if failures else 0


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
    parser.add_argument("--run-readonly", action="store_true",
                        help="drive the set in-process, read-only, against the "
                             "configured vault (snapshots and digests it first)")
    parser.add_argument("--artefacts",
                        help="directory for the snapshot and digests; must be "
                             "outside the repository")
    parser.add_argument("--project-id",
                        help="project id for the project-boost turn; resolved "
                             "from the corpus when omitted")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default="ember",
                        help="model name sent in the request body")
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args()

    if args.classify:
        return cmd_classify()
    if args.run:
        return cmd_run(args.base_url, args.model, args.timeout)
    if args.run_readonly:
        if not args.artefacts:
            parser.error("--run-readonly needs --artefacts")
        return cmd_run_readonly(Path(args.artefacts), args.project_id)
    parser.error("choose --classify, --run or --run-readonly")


if __name__ == "__main__":
    raise SystemExit(main())
