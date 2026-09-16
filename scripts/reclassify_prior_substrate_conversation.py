"""
scripts/reclassify_prior_substrate_conversation.py

ADR-015 amendment, implementation step 5 of 5: reclassify prior-substrate
conversation out of `ingested`.

The ChatGPT-era corpus (2022-12 to 2026-03, immediately preceding the
native vault's start, with no gap) is currently typed `ingested` in
ingested.db. It is not third-party material -- it is the record of one
continuous relationship that changed substrate partway through. This
script reclassifies it to `conversation`, with role marking (user turns
first-person, assistant turns marked per ADR-033's concern -- not
discarded, not laundered into first-person memory).

Why this moves rows between index files instead of flipping a column
--------------------------------------------------------------------
Retrieval routing (src/retrieval/semantic_search.py) is keyed on WHICH
FILE a row lives in, not solely on its stored memory_type column:
  - get_memory_items() -> semantic_search() queries memory.db once per
    entry in SQLITE_MEMORY_TYPES ({"conversation","profile","reflection",
    "journal"}). ingested.db is never consulted for those types.
  - semantic_search()'s ingested branch always queries ingested.db and
    unconditionally overwrites result["memory_type"] = "ingested" on
    every row it reads from that file, regardless of the row's own
    memory_type column.

Flipping memory_type in place without moving the row would make
TieringService (which reads the column directly, store-agnostic) treat a
row as conversation while retrieval kept reporting it as ingested on
every query -- manufacturing a NEW instance of exactly the "ranker and
tiering service hold contradictory classifications" defect this step
exists to close. So this script copies each qualifying row (embedding
BLOB, created_at, metadata, tier/heat/frequency/last_retrieved_at,
quality -- all carried over verbatim; memory_type set to 'conversation';
authorship recomputed) into memory.db, then deletes it from ingested.db.

Both files are SqliteVectorStore-backed indexes -- derived, rebuildable
artifacts (CLAUDE.md rule 4). Vault JSON is never touched (rule 3): the
canonical record still says "type": "ingested" on disk, same as every
other index-only migration in this codebase (see
scripts/rebuild_authorship_index.py, scripts/rebuild_authorship_reflections.py).

quality must be preserved explicitly. A meaningful fraction of this
corpus is already flagged quality='suppressed' from a prior cleanup
pass. memory.db may not have a `quality` column yet (it is not created
by SqliteVectorStore's own migrations, unlike tier/authorship) -- this
script adds it if missing, same idempotent ALTER-TABLE-try/except
pattern used everywhere else in this codebase. Losing this column would
silently un-suppress already-suppressed junk under the new type.

Selection is `memory_type='ingested' AND source='chatgpt'`. No separate
date-range filter is needed: verified against the live vault that
ingested.db currently contains ONLY this corpus (100% source='chatgpt'),
so the source filter alone is exact.

Role detection: any selected row whose metadata.role is not exactly
"user" or "assistant" is EXCLUDED from migration, left as `ingested`
unchanged, and counted separately as "skipped (unresolvable role)".
Nothing is guessed or defaulted.

Scope boundary: this migrates the EXISTING misfiled corpus only. It does
NOT change the ingest pipeline (src/ingest/writers.py, chunking.py) so
that FUTURE ChatGPT imports land as conversation directly -- the live
ingest writer today only writes to the legacy JSON index
(ingested_index.json via VectorIndex), never to ingested.db (SQLite);
nothing has landed in ingested.db since this corpus did. That mismatch
is the already-tracked "ingest writer and retrieval reader point at
different stores" defect (issue #174), explicitly out of scope for an
index-only migration. The general rule -- prior-substrate conversation
is conversation, and any future export-and-ingest of the same form
inherits it -- is documented here as guidance for whoever fixes #174
next, not implemented as pipeline code in this script.

Usage
-----
    python scripts/reclassify_prior_substrate_conversation.py              # dry-run, prints counts
    python scripts/reclassify_prior_substrate_conversation.py --confirm    # actually writes

    python scripts/reclassify_prior_substrate_conversation.py \\
        --ingested-db path/to/ingested.db --memory-db path/to/memory.db   # override (tests)

IMPORTANT: stop the API before running with --confirm (same warning as
scripts/rebuild_indexes.py) -- the API caches store handles in memory
(src/retrieval/store_cache.py) and won't see rows move until restarted.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.memory.authorship import classify_authorship  # noqa: E402
from src.retrieval.sqlite_vector_store import SqliteVectorStore  # noqa: E402

VALID_ROLES = {"user", "assistant"}

# Full column set copied verbatim from ingested.db's row into memory.db,
# except memory_type (forced to 'conversation') and authorship
# (recomputed). Order matches the SELECT/INSERT below.
_CARRIED_COLUMNS = (
    "id", "text", "embedding", "source", "created_at", "metadata",
    "quality", "tier", "last_retrieved_at", "retrieval_count",
    "importance_score", "heat_score", "frequency_score",
)


def _resolve_db_paths(ingested_override: Path | None, memory_override: Path | None) -> tuple[Path, Path]:
    from src.core.config import get_private_vault_path

    vault = get_private_vault_path()
    ingested_db = ingested_override or (vault / "embeddings" / "ingested.db")
    memory_db = memory_override or (vault / "embeddings" / "memory.db")
    return ingested_db, memory_db


def _ensure_memory_db_columns(memory_db: Path) -> None:
    """Guarantee memory.db has every column this migration writes.

    tier/last_retrieved_at/retrieval_count/importance_score/heat_score/
    frequency_score/authorship are created by SqliteVectorStore's own
    migrations -- opening it once is enough. `quality` is not managed by
    that class anywhere in the codebase (it's added ad hoc by suppression
    tooling), so it's ensured here explicitly.
    """
    store = SqliteVectorStore(memory_db)
    store.close()

    conn = sqlite3.connect(str(memory_db))
    try:
        conn.execute("ALTER TABLE vectors ADD COLUMN quality TEXT")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already exists
    finally:
        conn.close()


def _row_has_column(conn: sqlite3.Connection, column: str) -> bool:
    return any(row["name"] == column for row in conn.execute("PRAGMA table_info(vectors)"))


def reclassify(
    ingested_db: Path,
    memory_db: Path,
    confirm: bool,
) -> dict:
    """Migrate the prior-substrate conversation corpus.

    Returns a report dict: migrated counts by role, authorship counts
    assigned, quality counts carried, skipped-role count, and the
    ingested.db / memory.db type distributions before this call (always
    accurate) and after (exact under dry-run too, since selection is
    deterministic -- it just isn't written).
    """
    report: dict = {
        "migrated_user": 0,
        "migrated_assistant": 0,
        "skipped_unresolvable_role": 0,
        "authorship_assigned": {},
        "quality_carried": {},
        "before": {"ingested_db": {}, "memory_db": {}},
        "after": {"ingested_db": {}, "memory_db": {}},
    }

    if not ingested_db.exists():
        return report

    _ensure_memory_db_columns(memory_db)

    ingested_conn = sqlite3.connect(str(ingested_db))
    ingested_conn.row_factory = sqlite3.Row
    memory_conn = sqlite3.connect(str(memory_db))
    memory_conn.row_factory = sqlite3.Row

    def _type_distribution(conn: sqlite3.Connection) -> dict[str, int]:
        return {
            row["memory_type"]: row["c"]
            for row in conn.execute(
                "SELECT memory_type, COUNT(*) c FROM vectors GROUP BY memory_type"
            )
        }

    report["before"]["ingested_db"] = _type_distribution(ingested_conn)
    report["before"]["memory_db"] = _type_distribution(memory_conn)

    ingested_has_quality = _row_has_column(ingested_conn, "quality")
    ingested_has_frequency = _row_has_column(ingested_conn, "frequency_score")

    select_cols = [
        "id", "text", "embedding", "source", "created_at", "metadata",
        "tier", "last_retrieved_at", "retrieval_count", "importance_score",
        "heat_score",
    ]
    if ingested_has_quality:
        select_cols.append("quality")
    if ingested_has_frequency:
        select_cols.append("frequency_score")

    cursor = ingested_conn.execute(
        f"SELECT {', '.join(select_cols)} FROM vectors "
        "WHERE memory_type = 'ingested' AND source = 'chatgpt'"
    )

    migrated_ids: list[str] = []
    insert_rows: list[tuple] = []

    for row in cursor:
        try:
            metadata = json.loads(row["metadata"] or "{}")
        except (json.JSONDecodeError, TypeError):
            metadata = {}
        role = metadata.get("role")

        if role not in VALID_ROLES:
            report["skipped_unresolvable_role"] += 1
            continue

        authorship = classify_authorship("conversation", "chatgpt", metadata)
        report["authorship_assigned"][authorship] = report["authorship_assigned"].get(authorship, 0) + 1

        quality = row["quality"] if ingested_has_quality else None
        report["quality_carried"][quality] = report["quality_carried"].get(quality, 0) + 1

        frequency_score = row["frequency_score"] if ingested_has_frequency else 0.0

        insert_rows.append((
            row["id"], row["text"], row["embedding"], row["source"],
            "conversation", row["created_at"], row["metadata"],
            quality, row["tier"], row["last_retrieved_at"],
            row["retrieval_count"], row["importance_score"],
            row["heat_score"], frequency_score, authorship,
        ))
        migrated_ids.append(row["id"])

        if role == "user":
            report["migrated_user"] += 1
        else:
            report["migrated_assistant"] += 1

    if confirm and insert_rows:
        memory_conn.executemany(
            """
            INSERT OR IGNORE INTO vectors
                (id, text, embedding, source, memory_type, created_at, metadata,
                 quality, tier, last_retrieved_at, retrieval_count,
                 importance_score, heat_score, frequency_score, authorship)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            insert_rows,
        )
        memory_conn.commit()

        placeholders = ",".join("?" for _ in migrated_ids)
        if migrated_ids:
            ingested_conn.execute(
                f"DELETE FROM vectors WHERE id IN ({placeholders})",
                migrated_ids,
            )
            ingested_conn.commit()

    # "after" distribution: exact even under dry-run, since selection and
    # skip logic are deterministic -- it reflects what WOULD result.
    after_ingested = dict(report["before"]["ingested_db"])
    after_ingested["ingested"] = after_ingested.get("ingested", 0) - len(migrated_ids)
    if after_ingested.get("ingested", 0) <= 0:
        after_ingested.pop("ingested", None)
    report["after"]["ingested_db"] = after_ingested

    after_memory = dict(report["before"]["memory_db"])
    after_memory["conversation"] = after_memory.get("conversation", 0) + len(migrated_ids)
    report["after"]["memory_db"] = after_memory

    ingested_conn.close()
    memory_conn.close()

    return report


def _print_report(report: dict, confirm: bool, ingested_db: Path, memory_db: Path) -> None:
    mode = "WROTE" if confirm else "DRY-RUN"
    print(f"[RECLASSIFY] {mode}")
    print(f"  ingested.db: {ingested_db}")
    print(f"  memory.db:   {memory_db}")
    print()
    print(f"  migrated (user):      {report['migrated_user']:>6d}")
    print(f"  migrated (assistant): {report['migrated_assistant']:>6d}")
    print(f"  migrated (total):     {report['migrated_user'] + report['migrated_assistant']:>6d}")
    print(f"  skipped (unresolvable role): {report['skipped_unresolvable_role']:>6d}")
    print()
    print("  authorship assigned:")
    for label, count in sorted(report["authorship_assigned"].items()):
        print(f"    {label:13s} {count:>6d}")
    print()
    print("  quality carried over:")
    for label, count in sorted(report["quality_carried"].items(), key=lambda kv: str(kv[0])):
        print(f"    {str(label):13s} {count:>6d}")
    print()
    print(f"  ingested.db type distribution: before={report['before']['ingested_db']}  after={report['after']['ingested_db']}")
    print(f"  memory.db   type distribution: before={report['before']['memory_db']}  after={report['after']['memory_db']}")
    if not confirm:
        print()
        print("  Re-run with --confirm to persist. Stop the API first.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Write the migration. Without this flag, the script prints "
        "counts only (dry-run) and touches neither database.",
    )
    parser.add_argument(
        "--ingested-db", type=Path, default=None,
        help="Override the ingested index path. Defaults to the active vault's embeddings/ingested.db.",
    )
    parser.add_argument(
        "--memory-db", type=Path, default=None,
        help="Override the memory index path. Defaults to the active vault's embeddings/memory.db.",
    )
    args = parser.parse_args()

    ingested_db, memory_db = _resolve_db_paths(args.ingested_db, args.memory_db)

    if not ingested_db.exists():
        print(f"[RECLASSIFY] No SQLite index at {ingested_db}. Nothing to do.")
        return 0

    report = reclassify(ingested_db, memory_db, confirm=args.confirm)
    _print_report(report, args.confirm, ingested_db, memory_db)
    return 0


if __name__ == "__main__":
    sys.exit(main())
