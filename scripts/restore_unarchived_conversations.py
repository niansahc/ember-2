"""
scripts/restore_unarchived_conversations.py

Move conversation records out of memory/archive/ and back into
memory/conversation/, where the memory channel can reach them.

Why any record needs this. `memory/archive/` holds 2,735 records whose own
`type` field says `conversation`. `archive` is not in
`scripts/rebuild_indexes.py`'s MEMORY_DB_TYPES, and that rebuild enumerates by
DIRECTORY rather than by the type field, so none of them is indexed and none is
reachable by retrieval. 2,671 of them are a test corpus that
`scripts/cleanup_test_artifacts.py` swept aside on purpose, and they should stay
where they are. The rest were swept with them.

The selector is the absence of every marker the sweeping scripts leave behind:

    no metadata.test
    no metadata.deleted
    source != "cleanup_script"
    no "test_cleanup" tag

plus type == "conversation". On the production vault that selects 64 records, and
none of those 64 shares a session_id with any marked record, which is why they
read as real conversation rather than as fixtures the sweep forgot to label.

THE COUNT IS A POST-CONDITION, NOT A SELECTOR. `--expect N` asserts how many the
four conditions select and refuses to move anything if the number differs. That
direction matters: hardcoding 64 as the selector would quietly move a different
set on a drifted vault, while asserting it stops the run and says so.

Reversibility is not an afterthought here, it is why the manifest is written and
fsynced BEFORE the first move. An interrupted run leaves a complete record of
intent, so `--revert` can finish undoing what was started. The manifest carries
each file's SHA-256 and `--revert` refuses on a mismatch, because a manifest that
restores the wrong bytes is worse than none.

Filenames are restored to `<id>.json`. The sweep renamed them on the way in
(`cleanup_test_artifacts.archive_records`: `conversation__<id>.json`, and
`cleanup_uat.py`: `uat_cleanup_<folder>_<name>.json`), and
`resolve_source_records` resolves provenance by FILENAME. Leaving the prefixes
would index the records while leaving them unresolvable as a provenance source,
which is a half-restoration. The record body is never touched by this script.

Prints counts, field values and paths outside the vault. Never record content,
never a record id, never a session id. The manifest does carry ids and paths,
which is why it is written outside the repository and is not committed.

    python scripts/restore_unarchived_conversations.py                 # dry run
    python scripts/restore_unarchived_conversations.py --expect 64 --confirm \
        --manifest C:/EmberRestore/manifest.jsonl
    python scripts/restore_unarchived_conversations.py --revert C:/EmberRestore/manifest.jsonl
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Process entrypoint: load .env before any src import (see load_env_file).
if __name__ == "__main__":
    from src.core.config import load_env_file

    load_env_file()

from src.core.config import get_private_vault_path  # noqa: E402
from src.observability.guard_counters import assert_outside_repo  # noqa: E402

ARCHIVE_TYPE = "archive"
TARGET_TYPE = "conversation"

# The markers every sweeping script leaves. A record carrying any one of them was
# put in archive/ deliberately and is not ours to move.
MARKER_TAG = "test_cleanup"
MARKER_SOURCE = "cleanup_script"


@dataclass(frozen=True)
class Candidate:
    source_path: Path
    dest_path: Path
    record_id: str
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def is_unmarked_conversation(record: dict) -> bool:
    """True when a record in archive/ carries none of the four markers.

    One function, so the dry run, the real run and the tests cannot disagree
    about what the selection is. Each condition is its own statement rather than
    one boolean chain, so a test can delete any single marker from a fixture and
    see exactly which condition stopped reading it.
    """
    if str(record.get("type") or "") != TARGET_TYPE:
        return False
    metadata = record.get("metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    if metadata.get("test"):
        return False
    if metadata.get("deleted"):
        return False
    if str(record.get("source") or "") == MARKER_SOURCE:
        return False
    if MARKER_TAG in (record.get("tags") or []):
        return False
    return True


def collect(vault: Path) -> tuple[list[Candidate], dict[str, int]]:
    """Every unmarked conversation record in archive/, with its destination."""
    archive_dir = vault / "memory" / ARCHIVE_TYPE
    target_dir = vault / "memory" / TARGET_TYPE

    tally = {"files_scanned": 0, "unreadable": 0, "marked_or_other_type": 0}
    candidates: list[Candidate] = []

    for path in sorted(archive_dir.glob("*.json")):
        tally["files_scanned"] += 1
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            tally["unreadable"] += 1
            continue
        if not isinstance(record, dict) or not is_unmarked_conversation(record):
            tally["marked_or_other_type"] += 1
            continue

        # The in-file id is authoritative. The sweep prefixed the filename, so
        # the stem is not the id and must not be used as one.
        record_id = str(record.get("id") or "")
        if not record_id:
            tally["unreadable"] += 1
            continue

        candidates.append(Candidate(
            source_path=path,
            dest_path=target_dir / f"{record_id}.json",
            record_id=record_id,
            sha256=_sha256(path),
        ))

    return candidates, tally


def check_safe_to_move(candidates: list[Candidate]) -> list[str]:
    """Every reason not to proceed, collected rather than raised one at a time.

    Returning all of them means one run tells you everything that is wrong,
    instead of a sequence of runs each revealing the next problem.
    """
    problems: list[str] = []

    existing = [c for c in candidates if c.dest_path.exists()]
    if existing:
        problems.append(
            f"{len(existing)} destination filename(s) already exist in "
            f"memory/{TARGET_TYPE}/; moving would overwrite a live record"
        )

    dest_names = [c.dest_path.name for c in candidates]
    if len(dest_names) != len(set(dest_names)):
        problems.append(
            f"{len(dest_names) - len(set(dest_names))} duplicate destination "
            "filename(s) within the selection; two records claim one id"
        )

    ids = [c.record_id for c in candidates]
    if len(ids) != len(set(ids)):
        problems.append(
            f"{len(ids) - len(set(ids))} duplicate record id(s) within the selection"
        )

    return problems


def write_manifest(path: Path, candidates: list[Candidate]) -> None:
    """One line per intended move, flushed and fsynced before any move happens.

    The order is the point. A manifest written afterwards describes a completed
    run; this one describes an intended one, so an interruption is recoverable
    rather than a set that has to be reconstructed by inspection.
    """
    assert_outside_repo(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for candidate in candidates:
            handle.write(json.dumps({
                "record_id": candidate.record_id,
                "source_path": str(candidate.source_path),
                "dest_path": str(candidate.dest_path),
                "sha256": candidate.sha256,
            }) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def move_all(candidates: list[Candidate]) -> int:
    moved = 0
    for candidate in candidates:
        candidate.dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(candidate.source_path), str(candidate.dest_path))
        moved += 1
    return moved


def revert(manifest_path: Path) -> int:
    """Move every file in the manifest back to where it came from.

    Verifies the SHA before moving anything back. A manifest that restores the
    wrong bytes is worse than no manifest, because it is believed.
    """
    entries = [
        json.loads(line)
        for line in manifest_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    problems = 0
    plan: list[tuple[Path, Path]] = []
    for entry in entries:
        dest = Path(entry["dest_path"])
        source = Path(entry["source_path"])
        if not dest.exists():
            if source.exists():
                continue  # already reverted; idempotent
            print(f"  MISSING: a manifest entry is in neither location")
            problems += 1
            continue
        if _sha256(dest) != entry["sha256"]:
            print("  SHA MISMATCH: a file changed since the move; refusing")
            problems += 1
            continue
        plan.append((dest, source))

    if problems:
        print(f"  REFUSED: {problems} entry/entries did not verify. Nothing moved.")
        return -1

    reverted = 0
    for dest, source in plan:
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dest), str(source))
        reverted += 1
    return reverted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore unmarked conversation records from archive/ to "
                    "conversation/. Dry run unless --confirm.",
    )
    parser.add_argument("--confirm", action="store_true",
                        help="Actually move the records (default: dry run)")
    parser.add_argument("--expect", type=int,
                        help="Assert the selection is exactly this many records "
                             "and refuse to move anything if it is not")
    parser.add_argument("--manifest",
                        help="Where to write the reversal manifest. Required "
                             "with --confirm, and must be outside the repository")
    parser.add_argument("--revert",
                        help="Undo a previous run from its manifest")
    parser.add_argument("--vault", help="Override the vault path")
    args = parser.parse_args()

    if args.revert:
        count = revert(Path(args.revert))
        if count < 0:
            return 2
        print(f"  reverted {count} record(s) to memory/{ARCHIVE_TYPE}/")
        return 0

    vault = Path(args.vault) if args.vault else get_private_vault_path()
    candidates, tally = collect(vault)

    print(f"Vault: {vault}")
    print(f"  archive/ files scanned            : {tally['files_scanned']}")
    print(f"  marked, or not type=conversation  : {tally['marked_or_other_type']}")
    print(f"  unreadable or missing an id       : {tally['unreadable']}")
    print(f"  SELECTED (no marker of any kind)  : {len(candidates)}")

    problems = check_safe_to_move(candidates)
    for problem in problems:
        print(f"  REFUSED: {problem}")
    if problems:
        return 2

    if args.expect is not None and len(candidates) != args.expect:
        print(f"  REFUSED: expected {args.expect} record(s), the four conditions "
              f"select {len(candidates)}. Nothing moved.")
        return 2

    print(f"  destination collisions            : 0")
    print(f"  duplicate ids within selection    : 0")

    if not args.confirm:
        print("\n  dry run: nothing moved. Re-run with --expect N --confirm "
              "--manifest PATH to act.")
        return 0

    if not args.manifest:
        print("  REFUSED: --manifest is required for a real run, so the move "
              "can be undone without reconstructing the set.")
        return 2

    manifest = Path(args.manifest)
    try:
        write_manifest(manifest, candidates)
    except ValueError as exc:
        print(f"  REFUSED: {exc}")
        return 2
    print(f"\n  manifest written and fsynced before any move: {manifest}")

    moved = move_all(candidates)
    print(f"  moved {moved} record(s) to memory/{TARGET_TYPE}/")
    print(f"  revert with: python {Path(__file__).name} --revert {manifest}")
    print("  the index is now behind the vault; rebuild with "
          "scripts/rebuild_indexes.py --memory-db")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
