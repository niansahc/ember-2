"""
scripts/cleanup_today_test_sessions.py

One-time cleanup: soft-delete all sessions created on 2026-03-28,
EXCEPT any session whose title contains "Tell me about yourself".

Usage:
    python scripts/cleanup_today_test_sessions.py --dry-run
    python scripts/cleanup_today_test_sessions.py
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
TARGET_DATE = "2026-03-28"
KEEP_TITLE = "Tell me about yourself"


def main():
    dry_run = "--dry-run" in sys.argv
    vault = get_private_vault_path()
    session_dir = vault / "memory" / "session"
    conv_dir = vault / "memory" / "conversation"

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

    # Filter to today's sessions, exclude already-deleted and the keep title
    to_delete = []
    skipped = []
    for sid, rec in by_sid.items():
        meta = rec.get("metadata", {})
        if meta.get("deleted", False):
            continue
        created = meta.get("created_at", rec.get("timestamp", ""))
        if TARGET_DATE not in created:
            continue
        title = rec.get("text", "")
        if KEEP_TITLE.lower() in title.lower():
            skipped.append((sid, title, "kept — title match"))
            continue
        to_delete.append((sid, title, rec))

    print(f"Vault: {vault}")
    print(f"Target date: {TARGET_DATE}")
    print(f"Keeping sessions with title containing: \"{KEEP_TITLE}\"")
    print()

    if skipped:
        print(f"Skipped ({len(skipped)}):")
        for sid, title, reason in skipped:
            print(f"  {sid}: {title[:60]} — {reason}")
        print()

    if not to_delete:
        print("No sessions to delete.")
        return

    # Find conversation turns for sessions to delete
    delete_sids = {sid for sid, _, _ in to_delete}
    turns_to_delete = []
    if conv_dir.exists():
        for f in storage.list_memory_files(conv_dir):
            try:
                rec = storage.read_json(f)
            except (json.JSONDecodeError, OSError):
                continue
            sid = rec.get("metadata", {}).get("session_id", "")
            if sid in delete_sids:
                role = rec.get("metadata", {}).get("role", "?")
                text_preview = rec.get("text", "")[:50]
                turns_to_delete.append((sid, role, text_preview, rec))

    print(f"Sessions to delete ({len(to_delete)}):")
    for sid, title, _ in to_delete:
        turn_count = sum(1 for s, _, _, _ in turns_to_delete if s == sid)
        print(f"  {sid}: {title[:60]} ({turn_count} turns)")
    print()
    print(f"Total: {len(to_delete)} sessions, {len(turns_to_delete)} conversation turns")
    print()

    if dry_run:
        print("Dry run — no changes made.")
        return

    answer = input(f"Delete {len(to_delete)} sessions and {len(turns_to_delete)} turns? [y/N] ")
    if answer.strip().lower() != "y":
        print("Cancelled.")
        return

    # Soft-delete sessions
    deleted_sessions = 0
    for sid, title, orig_rec in to_delete:
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%dT%H-%M-%S-%f")
        meta = orig_rec.get("metadata", {}).copy()
        meta["deleted"] = True
        record = {
            "id": ts,
            "timestamp": now.isoformat(),
            "type": "session",
            "text": title,
            "source": "cleanup_script",
            "tags": ["session", "cleanup_today"],
            "metadata": meta,
        }
        file_path = session_dir / f"{ts}.json"
        storage.write_json(file_path, record)
        deleted_sessions += 1

    # Soft-delete conversation turns
    deleted_turns = 0
    for sid, role, _, orig_rec in turns_to_delete:
        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y-%m-%dT%H-%M-%S-%f")
        meta = orig_rec.get("metadata", {}).copy()
        meta["deleted"] = True
        record = {
            "id": ts,
            "timestamp": now.isoformat(),
            "type": "conversation",
            "text": orig_rec.get("text", ""),
            "source": "cleanup_script",
            "tags": orig_rec.get("tags", []) + ["cleanup_today"],
            "metadata": meta,
        }
        file_path = conv_dir / f"{ts}.json"
        storage.write_json(file_path, record)
        deleted_turns += 1

    print(f"Soft-deleted {deleted_sessions} sessions and {deleted_turns} conversation turns.")
    print("Original records preserved — only delete markers appended.")


if __name__ == "__main__":
    main()
