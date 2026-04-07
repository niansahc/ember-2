"""
One-time vault hygiene script: soft-delete orphan sessions.

An "orphan session" is a session record whose conversation memory contains
zero user turns. These were created during a window where short user turns
were silently dropped by a length filter and/or lost to filename collisions
in the same wall-clock second. The write-side bugs were fixed in commit
aa56908 (2026-03-28); sessions created after that point are healthy.

Default cutoff: 2026-04-01 00:00 UTC. Sessions created on or after the
cutoff are never touched, regardless of their user-turn count, because a
post-fix session with zero user turns is almost certainly a brand-new
session that hasn't been written to yet.

Soft-delete only — append-only, reuses src.memory.session.delete_session().

Usage:
    python scripts/cleanup_orphan_sessions.py --dry-run
    python scripts/cleanup_orphan_sessions.py --execute
    python scripts/cleanup_orphan_sessions.py --execute --cutoff 2026-04-01

Default mode is --dry-run. The script will refuse to write unless --execute
is passed explicitly.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# Make src/ importable when run from repo root.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.core.config import get_private_vault_path  # noqa: E402
from src.memory import session as session_module  # noqa: E402


DEFAULT_CUTOFF = "2026-04-01"


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO timestamp leniently. Returns None on failure."""
    if not value:
        return None
    try:
        # Handle trailing Z
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _load_json(path: Path) -> dict | None:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def find_orphan_sessions(vault: Path, cutoff: datetime) -> list[dict]:
    """
    Return a list of orphan session descriptors.

    A session is orphan iff:
      - It has a session record in memory/session/
      - It is not already soft-deleted
      - Its created_at is strictly before the cutoff
      - Its conversation memory has zero records with role == "user"
    """
    conv_dir = vault / "memory" / "conversation"
    sess_dir = vault / "memory" / "session"

    # Build per-session role counts from conversation records.
    role_counts: dict[str, Counter] = {}
    for path in conv_dir.glob("*.json"):
        record = _load_json(path)
        if record is None:
            continue
        meta = record.get("metadata") or {}
        sid = meta.get("session_id")
        if not sid:
            continue
        role = meta.get("role") or record.get("role") or "<unknown>"
        role_counts.setdefault(sid, Counter())[role] += 1

    # Build the latest session record per session_id (append-only history).
    latest_session: dict[str, dict] = {}
    for path in sess_dir.glob("*.json"):
        record = _load_json(path)
        if record is None:
            continue
        meta = record.get("metadata") or {}
        sid = meta.get("session_id")
        if not sid:
            continue
        # Newer record wins.
        existing = latest_session.get(sid)
        if existing is None or record.get("timestamp", "") > existing.get("timestamp", ""):
            latest_session[sid] = record

    orphans: list[dict] = []
    for sid, sess_record in latest_session.items():
        meta = sess_record.get("metadata") or {}
        if meta.get("deleted"):
            continue  # already soft-deleted
        created_at = _parse_iso(meta.get("created_at", ""))
        if created_at is None:
            continue  # cannot determine age, skip defensively
        # Normalize to aware UTC for comparison.
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        if created_at >= cutoff:
            continue  # post-fix session, leave alone
        counts = role_counts.get(sid, Counter())
        user_count = counts.get("user", 0)
        if user_count > 0:
            continue  # healthy
        orphans.append(
            {
                "session_id": sid,
                "created_at": created_at.isoformat(),
                "assistant_count": counts.get("assistant", 0),
                "total_records": sum(counts.values()),
            }
        )

    orphans.sort(key=lambda o: o["created_at"])
    return orphans


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Print what would be deleted without writing (default).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually perform the soft-deletes. Required to write.",
    )
    parser.add_argument(
        "--cutoff",
        default=DEFAULT_CUTOFF,
        help=f"ISO date (YYYY-MM-DD). Sessions created on/after this are skipped. Default: {DEFAULT_CUTOFF}",
    )
    args = parser.parse_args()

    cutoff_dt = _parse_iso(args.cutoff)
    if cutoff_dt is None:
        print(f"ERROR: invalid --cutoff value: {args.cutoff!r}", file=sys.stderr)
        return 2
    if cutoff_dt.tzinfo is None:
        cutoff_dt = cutoff_dt.replace(tzinfo=timezone.utc)

    vault = get_private_vault_path()
    print(f"Vault:   {vault}")
    print(f"Cutoff:  {cutoff_dt.isoformat()} (sessions created on/after this are skipped)")
    print(f"Mode:    {'EXECUTE' if args.execute else 'DRY-RUN'}")
    print()

    orphans = find_orphan_sessions(vault, cutoff_dt)
    print(f"Found {len(orphans)} orphan session(s) (zero user turns, pre-cutoff, not already deleted).")
    print()

    if not orphans:
        print("Nothing to do.")
        return 0

    # Summary table — first 20, then a tally.
    print(f"{'session_id':<28} {'created_at':<32} {'assistant':>10} {'total':>7}")
    print("-" * 80)
    for o in orphans[:20]:
        print(f"{o['session_id']:<28} {o['created_at']:<32} {o['assistant_count']:>10} {o['total_records']:>7}")
    if len(orphans) > 20:
        print(f"... and {len(orphans) - 20} more")
    print()

    if not args.execute:
        print("DRY-RUN — no changes written. Re-run with --execute to apply.")
        return 0

    # Execute path: call the existing soft-delete helper for each session.
    deleted = 0
    failed = 0
    for o in orphans:
        try:
            result = session_module.delete_session(o["session_id"])
            if result is None:
                failed += 1
                print(f"  SKIP {o['session_id']} (delete_session returned None)")
            else:
                deleted += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  FAIL {o['session_id']}: {exc}")

    print()
    print(f"Soft-deleted: {deleted}")
    print(f"Failed:       {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
