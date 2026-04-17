"""
scripts/cleanup_uat.py

Archive vault records created during a recent UAT run.

Targets all record types that UAT contaminates: conversation turns,
auto-extracted state records, session reflections, commitment/task
records, and deviation records written by background threads during the
UAT window. Profile and lodestone records are excluded — they predate
any UAT run and should never be touched.

Default window: records created in the last 2 hours. Override with
--hours N.

Dry-run by default. Pass --confirm to actually move files to archive/.

Append-only compliant: files are moved to private_vault/memory/archive/,
not deleted or mutated.

Usage:
    python scripts/cleanup_uat.py                   # dry-run, last 2h
    python scripts/cleanup_uat.py --hours 4         # dry-run, last 4h
    python scripts/cleanup_uat.py --confirm         # archive for real
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path


# Memory type folders to scan. Excludes profile, lodestone, ingested
# (those are never UAT-generated), and archive (the destination).
TARGET_FOLDERS = (
    "conversation",
    "state",
    "session",
    "reflection",
    "task",
    "deviation",
)


def _resolve_vault() -> Path:
    try:
        from src.core.config import get_private_vault_path
        return get_private_vault_path()
    except Exception:
        pass
    import os
    env_path = os.getenv("PRIVATE_VAULT_PATH")
    if env_path:
        return Path(env_path)
    print("[ERROR] Cannot resolve vault path. Set PRIVATE_VAULT_PATH or run from the repo root.")
    sys.exit(1)


def _parse_timestamp_from_filename(filename: str) -> datetime | None:
    """Extract a datetime from the vault filename convention.

    Filenames look like: 2026-04-15T01-38-22-123456.json or
    2026-04-15T01-38-22_active_project.json. We parse the leading
    datetime portion.
    """
    stem = filename.replace(".json", "")
    # Trim any suffix after the timestamp (e.g. _active_project, _open_loop)
    # Timestamps are always 19+ chars: 2026-04-15T01-38-22
    if len(stem) < 19:
        return None
    ts_part = stem[:19]
    try:
        return datetime.strptime(ts_part, "%Y-%m-%dT%H-%M-%S")
    except ValueError:
        # Some files use different precision — try without seconds
        try:
            return datetime.strptime(ts_part[:16], "%Y-%m-%dT%H-%M")
        except ValueError:
            return None


def _parse_timestamp_from_record(file_path: Path) -> datetime | None:
    """Fallback: read the JSON record and parse the timestamp field."""
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        ts = data.get("timestamp", "")
        if not ts:
            return None
        # Normalise hyphenated time: 2026-04-15T01-38-22 → 2026-04-15T01:38:22
        parts = ts.split("T")
        if len(parts) == 2:
            time_components = parts[1].split("-")
            if len(time_components) >= 3:
                iso = f"{parts[0]}T{time_components[0]}:{time_components[1]}:{time_components[2]}"
                return datetime.fromisoformat(iso)
    except Exception:
        pass
    return None


def scan(vault: Path, cutoff: datetime) -> dict[str, list[Path]]:
    """Return {folder_name: [paths]} of files newer than cutoff."""
    results: dict[str, list[Path]] = {}
    for folder_name in TARGET_FOLDERS:
        folder = vault / "memory" / folder_name
        if not folder.is_dir():
            continue
        hits: list[Path] = []
        for f in sorted(folder.glob("*.json")):
            ts = _parse_timestamp_from_filename(f.name)
            if ts is None:
                ts = _parse_timestamp_from_record(f)
            if ts is not None and ts >= cutoff:
                hits.append(f)
        if hits:
            results[folder_name] = hits
    return results


def archive(files_by_folder: dict[str, list[Path]], vault: Path, confirm: bool) -> int:
    """Move matched files to archive/. Returns total count."""
    archive_dir = vault / "memory" / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for folder_name, paths in sorted(files_by_folder.items()):
        for p in paths:
            dest = archive_dir / f"uat_cleanup_{folder_name}_{p.name}"
            if confirm:
                shutil.move(str(p), str(dest))
            total += 1
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours", type=float, default=2.0,
        help="Window in hours to scan back from now (default: 2).",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="Actually archive. Without this, dry-run only.",
    )
    args = parser.parse_args()

    vault = _resolve_vault()
    cutoff = datetime.now() - timedelta(hours=args.hours)

    print(f"[UAT CLEANUP] Vault: {vault}")
    print(f"[UAT CLEANUP] Window: last {args.hours}h (since {cutoff.strftime('%Y-%m-%d %H:%M')})")
    print()

    hits = scan(vault, cutoff)
    if not hits:
        print("No records found in the cleanup window. Nothing to do.")
        return 0

    total = 0
    for folder_name, paths in sorted(hits.items()):
        print(f"  {folder_name:15s}  {len(paths):>4d} records")
        total += len(paths)
    print(f"  {'TOTAL':15s}  {total:>4d} records")
    print()

    if args.confirm:
        archived = archive(hits, vault, confirm=True)
        print(f"[UAT CLEANUP] Archived {archived} records to memory/archive/.")
    else:
        print("[UAT CLEANUP] DRY RUN — no files moved. Re-run with --confirm to archive.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
