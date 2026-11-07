"""
scripts/backfill_ingested_index.py

Index ingested chunks that the pre-#174 writer left unreachable.

Until issue #174 the ingest writer saved embeddings to
embeddings/ingested_index.json, which retrieval never reads. Every chunk it
wrote since the v0.6.0 SQLite migration has a canonical file in
memory/ingested/ and no row in a store retrieval reads. This script gives
those chunks the rows the fixed writer would have written. Records typed
`ingested` that /ingest/json wrote through write_memory had the same
defect, live in the same directory, and are covered too.

Source of truth
---------------
The canonical chunk files, not the JSON index (CLAUDE.md rule 4: derived
artifacts are rebuilt from canonical records). The JSON index may hold
embeddings from a retired embedding model, and it can be past its 50 MB load
guard. It is read only as a cross-check: how many of its entries have a
canonical file, and how many of those are indexed (or held out by ADR-033)
afterwards. It is never modified.

Each chunk file goes through src/ingest/writers.index_chunks, the live
writer's own routine: the ADR-033 assistant gate, the ADR-015 routing
(ChatGPT user turns to memory.db as conversation, everything else to
ingested.db), and the content-key skip. The skip is what makes a run
idempotent and what keeps the corpus already in memory.db (migrated under
file-path ids, and partly quality-suppressed) from being indexed twice or
un-suppressed.

Usage
-----
    python scripts/backfill_ingested_index.py                  # dry run, active vault
    python scripts/backfill_ingested_index.py --vault PATH     # dry run, another vault
    python scripts/backfill_ingested_index.py --vault PATH --confirm
    python scripts/backfill_ingested_index.py --confirm --live-vault

--confirm writes only when the vault is VAULT_PATH_TEST, unless --live-vault
is also given. A live-vault run is the user's to make, with the API stopped:
the API caches store handles (src/retrieval/store_cache.py) and its content
keys go stale under a concurrent writer.

Output is counts only. Chunk ids and text are vault content and are never
printed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Process entrypoint: load .env before any src import (see load_env_file).
if __name__ == "__main__":
    from src.core.config import load_env_file

    load_env_file()

from src.core.config import get_private_vault_path, resolve_vault_path  # noqa: E402
from src.core.jsonio import safe_read_json  # noqa: E402
from src.ingest.index_routing import index_target, is_indexable  # noqa: E402
from src.ingest.models import ChunkedDocument  # noqa: E402
from src.ingest.writers import (  # noqa: E402
    DOCUMENT_ONLY_METADATA,
    chunk_file_path,
    index_chunks,
    ingested_dir,
)
from src.memory.eval_fixtures import is_test_vault  # noqa: E402
from src.memory.write_memory import should_index  # noqa: E402
from src.retrieval.sqlite_vector_store import content_key  # noqa: E402
from src.retrieval.vector_index import VectorIndex  # noqa: E402


def load_chunk_files(vault: Path) -> tuple[list[ChunkedDocument], int, int]:
    """Every indexable canonical file in memory/ingested/.

    Returns (chunks, unreadable, filtered). Two writers put files here:

      - the ingest writer: chunk files with `content` and `chunk_id`.
      - write_memory, for records typed `ingested` (/ingest/json): records
        with `text`, `id` and `timestamp`. On the live path these pass
        write_memory's should_index filter before indexing, so the backfill
        applies it too; a record it rejects is counted as filtered.
    """
    chunks: list[ChunkedDocument] = []
    unreadable = 0
    filtered = 0
    directory = ingested_dir(vault)
    if not directory.exists():
        return chunks, unreadable, filtered
    for path in sorted(directory.glob("*.json")):
        data = safe_read_json(path, default=None)
        if not isinstance(data, dict):
            unreadable += 1
            continue
        is_record = "content" not in data and "text" in data
        content = data.get("text") if is_record else data.get("content")
        if not isinstance(content, str) or not content.strip():
            unreadable += 1
            continue
        if is_record and not should_index({**data, "type": "ingested"}, vault):
            filtered += 1
            continue
        chunks.append(ChunkedDocument(
            source=str(data.get("source") or ""),
            doc_id=str(data.get("doc_id") or ""),
            chunk_id=str(data.get("chunk_id") or data.get("id") or path.stem),
            title=str(data.get("title") or ""),
            created_at=data.get("created_at") or data.get("timestamp"),
            content=content,
            # A ChatGPT chunk carries its whole conversation's messages and
            # roles; nothing here reads them, and across an export they grow
            # with turns times conversation length.
            metadata={k: v for k, v in (data.get("metadata") or {}).items()
                      if k not in DOCUMENT_ONLY_METADATA},
        ))
    return chunks, unreadable, filtered


def json_index_cross_check(vault: Path, chunks: list[ChunkedDocument], planned: dict[str, set[str]]) -> dict:
    """How the old JSON index's entries map onto canonical files and stores.

    An entry is matched to its chunk file by file name: the absolute path it
    recorded can predate a vault move. `covered` counts entries whose chunk
    is indexed in its target store or held out by ADR-033.
    """
    index_path = vault / "embeddings" / "ingested_index.json"
    report = {"entries": 0, "with_chunk_file": 0, "without_chunk_file": 0,
              "covered": 0, "readable": True}
    if not index_path.exists():
        return report
    index = VectorIndex()
    entries, readable = index._read_disk(index_path)
    report["readable"] = readable
    by_name = {chunk_file_path(vault, chunk.chunk_id).name: chunk for chunk in chunks}
    for entry in entries:
        report["entries"] += 1
        name = Path(str(entry.get("file_path") or "")).name
        chunk = by_name.get(name)
        if chunk is None:
            report["without_chunk_file"] += 1
            continue
        report["with_chunk_file"] += 1
        if not is_indexable(chunk.metadata):
            report["covered"] += 1
            continue
        target = index_target(chunk.source, chunk.metadata)
        if content_key(chunk.content) in planned.get(target.db_name, ()):
            report["covered"] += 1
    return report


def backfill(vault: Path, *, confirm: bool) -> dict:
    """Plan (and with confirm, run) the backfill for one vault. Counts only."""
    chunks, unreadable, filtered = load_chunk_files(vault)
    # What the stores hold (or, on a dry run, would hold) afterwards, for the
    # cross-check: what is there now plus what this run adds.
    planned: dict[str, set[str]] = {}
    counts = index_chunks(chunks, vault, dry_run=not confirm, known_keys=planned)

    return {
        "chunk_files": len(chunks),
        "unreadable_chunk_files": unreadable,
        "filtered": filtered,
        "indexed": {"ingested.db": counts["ingested.db"], "memory.db": counts["memory.db"]},
        "already_indexed": counts["already_indexed"],
        "assistant_gated": counts["assistant_gated"],
        "json_index": json_index_cross_check(vault, chunks, planned),
    }


def _print_report(report: dict, confirm: bool) -> None:
    verb = "indexed" if confirm else "would index"
    print(f"  chunk files read           : {report['chunk_files']}")
    print(f"  chunk files unreadable     : {report['unreadable_chunk_files']}")
    print(f"  records the filter rejects : {report['filtered']}")
    print(f"  {verb} into ingested.db : {report['indexed']['ingested.db']}")
    print(f"  {verb} into memory.db   : {report['indexed']['memory.db']}")
    print(f"  already indexed (skipped)  : {report['already_indexed']}")
    print(f"  held out by ADR-033        : {report['assistant_gated']}")
    j = report["json_index"]
    print(f"  JSON index entries         : {j['entries']} (readable: {j['readable']})")
    print(f"    with a chunk file        : {j['with_chunk_file']}")
    print(f"    without a chunk file     : {j['without_chunk_file']}")
    print(f"    covered afterwards       : {j['covered']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index ingested chunks left unreachable by #174")
    parser.add_argument("--vault", help="Vault to backfill (default: the active vault)")
    parser.add_argument("--confirm", action="store_true", help="Write; without it this is a dry run")
    parser.add_argument("--live-vault", action="store_true",
                        help="Allow --confirm on a vault that is not VAULT_PATH_TEST")
    args = parser.parse_args(argv)

    vault = resolve_vault_path(args.vault) if args.vault else get_private_vault_path()
    if vault is None or not Path(vault).is_dir():
        print("FATAL: vault directory not found")
        return 1
    vault = Path(vault)

    if args.confirm and not args.live_vault and not is_test_vault(vault):
        print("FATAL: --confirm writes only to VAULT_PATH_TEST. This vault is not it. "
              "Pass --live-vault to backfill another vault, with the API stopped.")
        return 2

    print(f"Backfill ingested index ({'confirm' if args.confirm else 'dry run'})")
    report = backfill(vault, confirm=args.confirm)
    _print_report(report, args.confirm)
    if not args.confirm:
        print("  dry run: nothing embedded, nothing written")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
