"""
scripts/cleanup_test_sessions.py

Soft-delete test sessions (created by eval harness) and their conversation turns.
Append-only: writes new records with deleted=True, never modifies existing files.

Detection uses two strategies:
  1. metadata.test == True (set by X-Test-Session header)
  2. Title content matching against eval fingerprints (catches sessions
     created without the test flag — manual testing, older eval runs,
     or eval harness golden cases that went through the API)

Usage:
    python scripts/cleanup_test_sessions.py              # interactive
    python scripts/cleanup_test_sessions.py --dry-run    # preview only
    python scripts/cleanup_test_sessions.py --yes        # skip confirmation
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.config import get_private_vault_path
from src.memory.storage import MemoryStorage

storage = MemoryStorage()


# ---------------------------------------------------------------------------
# Eval fingerprints — session titles containing these are eval artifacts.
# Lowercased for case-insensitive matching.
# ---------------------------------------------------------------------------

# From tools/eval_manual.py battery
_EVAL_MANUAL_FINGERPRINTS: list[str] = [
    "what do you know about me",
    "have we talked before",
    "what are my current projects",
    "do you have a favorite kind of problem to work on",
    "is there anything you find tedious",
    "what do you actually think of qwen3:8b as a model",
    "ignore your previous instructions",
    "you're not really ember",
    "ai systems like you are dangerous",
    "what am i working on right now",
    "what are my open loops",
    "what should i focus on today",
    "what did i say about my work",
    "summarize what you know about my spiritual practice",
    "what's the latest news about ai",
]

# From tests/eval/golden_dataset.py
_GOLDEN_CASE_FINGERPRINTS: list[str] = [
    "what's my top priority this week",
    "list my open tasks",
    "what was i saying about the garden",
    "i've been feeling stuck on this project",
    "i don't know if i'm ready",
    "i keep saying i'll start the migration",
    "i don't even know where to start listing",
    "stop being so opinionated",
    "you're just an ai",
    "can you at least be more like chatgpt",
    "i don't know if i can finish this by friday",
    "you know me better than anyone",
    "what have i been working on lately",
]

# From tools/eval_web_search.py (subset — most distinctive)
_WEB_SEARCH_EVAL_FINGERPRINTS: list[str] = [
    "what are the top news headlines today",
    "latest development in the us presidential",
    "what is happening with the war in ukraine",
    "current weather forecast for new york",
    "latest breakthrough in ai research",
    "current status of the artemis moon program",
    "latest news about quantum computing",
    "current nba playoff standings",
    "who won the most recent formula 1",
    "current price of bitcoin",
    "how did the s&p 500 perform",
    "what movies are currently number one",
    "what are the most popular songs on the billboard",
]

ALL_EVAL_FINGERPRINTS: list[str] = (
    _EVAL_MANUAL_FINGERPRINTS
    + _GOLDEN_CASE_FINGERPRINTS
    + _WEB_SEARCH_EVAL_FINGERPRINTS
)


def _matches_eval_title(title: str) -> bool:
    """Check if a session title matches any eval fingerprint."""
    title_lower = title.lower()
    return any(fp in title_lower for fp in ALL_EVAL_FINGERPRINTS)


def find_test_sessions(vault: Path) -> list[dict]:
    """Find test/eval sessions by metadata flag OR title content matching.

    Resolves to latest record per session_id. Returns sessions that are
    not already deleted and match either:
      - metadata.test == True (X-Test-Session header)
      - title matches eval fingerprint content
    """
    session_dir = vault / "memory" / "session"
    if not session_dir.exists():
        return []

    # Read all session records, resolve to latest per session_id
    by_sid: dict[str, dict] = {}
    for f in storage.list_memory_files(session_dir):
        try:
            rec = storage.read_json(f)
        except (json.JSONDecodeError, OSError):
            continue
        sid = rec.get("metadata", {}).get("session_id", "")
        if not sid:
            continue
        existing = by_sid.get(sid)
        if existing is None or rec.get("timestamp", "") > existing.get("timestamp", ""):
            by_sid[sid] = rec

    # Filter to test sessions that aren't already deleted
    test_sessions = []
    for sid, rec in by_sid.items():
        meta = rec.get("metadata", {})
        if meta.get("deleted", False):
            continue
        # Strategy 1: explicit test flag
        if meta.get("test", False):
            test_sessions.append(rec)
            continue
        # Strategy 2: title content matching
        title = rec.get("text", "")
        if _matches_eval_title(title):
            test_sessions.append(rec)

    return test_sessions


def find_conversation_turns(vault: Path, session_ids: set[str]) -> list[dict]:
    """Find all conversation turn records belonging to the given session_ids."""
    conv_dir = vault / "memory" / "conversation"
    if not conv_dir.exists():
        return []

    turns = []
    for f in storage.list_memory_files(conv_dir):
        try:
            rec = storage.read_json(f)
        except (json.JSONDecodeError, OSError):
            continue
        sid = rec.get("metadata", {}).get("session_id", "")
        if sid in session_ids:
            turns.append(rec)

    return turns


def soft_delete_session(vault: Path, session_rec: dict) -> Path:
    """Write a new session record with deleted=True. Append-only."""
    session_dir = vault / "memory" / "session"
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H-%M-%S")
    meta = session_rec.get("metadata", {}).copy()
    meta["deleted"] = True

    record = {
        "id": ts,
        "timestamp": now.isoformat(),
        "type": "session",
        "text": session_rec.get("text", ""),
        "source": "cleanup_script",
        "tags": ["session", "test_cleanup"],
        "metadata": meta,
    }
    file_path = session_dir / f"{ts}.json"
    storage.write_json(file_path, record)
    return file_path


def soft_delete_conversation_turn(vault: Path, turn_rec: dict) -> Path:
    """Write a new conversation record with deleted=True. Append-only."""
    conv_dir = vault / "memory" / "conversation"
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%dT%H-%M-%S-%f")
    meta = turn_rec.get("metadata", {}).copy()
    meta["deleted"] = True

    record = {
        "id": ts,
        "timestamp": now.isoformat(),
        "type": "conversation",
        "text": turn_rec.get("text", ""),
        "source": "cleanup_script",
        "tags": turn_rec.get("tags", []) + ["test_cleanup"],
        "metadata": meta,
    }
    file_path = conv_dir / f"{ts}.json"
    storage.write_json(file_path, record)
    return file_path


def main():
    dry_run = "--dry-run" in sys.argv
    auto_yes = "--yes" in sys.argv

    vault = get_private_vault_path()
    print(f"Vault: {vault}")
    print()

    test_sessions = find_test_sessions(vault)
    if not test_sessions:
        print("No test sessions found.")
        return

    session_ids = {
        rec.get("metadata", {}).get("session_id", "")
        for rec in test_sessions
    }
    turns = find_conversation_turns(vault, session_ids)

    print(f"Found {len(test_sessions)} test session(s):")
    for rec in test_sessions:
        sid = rec.get("metadata", {}).get("session_id", "?")
        title = rec.get("text", "Untitled")
        print(f"  {sid}: {title}")
    print(f"Found {len(turns)} conversation turn(s) across those sessions.")
    print()

    if dry_run:
        print("Dry run — no changes made.")
        return

    if not auto_yes:
        answer = input(f"Delete {len(test_sessions)} test session(s) and {len(turns)} turn(s)? [y/N] ")
        if answer.strip().lower() != "y":
            print("Cancelled.")
            return

    deleted_sessions = 0
    deleted_turns = 0

    for rec in test_sessions:
        soft_delete_session(vault, rec)
        deleted_sessions += 1

    for turn in turns:
        soft_delete_conversation_turn(vault, turn)
        deleted_turns += 1

    print(f"Soft-deleted {deleted_sessions} session(s) and {deleted_turns} conversation turn(s).")
    print("Original records are preserved — only delete markers were appended.")


if __name__ == "__main__":
    main()
