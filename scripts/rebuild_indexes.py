"""
scripts/rebuild_indexes.py

Full index rebuild for Ember-2.

Rebuilds all vector indexes from canonical vault records using the
current embedding model. Run this after changing the embedding model
(e.g. switching from sentence-transformers to nomic-embed-text).

Indexes are derived artifacts — they can always be deleted and rebuilt
from the canonical JSON records in private_vault/memory/. This script
is the rebuild mechanism.

memory.db and the reproducibility rule
--------------------------------------
That guarantee (CLAUDE.md core rule 4) did not hold for memory.db until
--memory-db was added, and the reason is worth stating because it drives
the mode's design. memory.db holds primary-key identity that exists
nowhere else: the prior-substrate conversation corpus was moved into it
from ingested.db by an index-only migration, carrying ids minted by a
retired ingest pipeline. Those ids appear in no canonical record. The
vault-canonical id for the same content is the chunk_id in
memory/ingested/.

So a rebuild cannot reproduce memory.db's identity from the vault alone.
What it can do is preserve it: source records are matched to existing
rows by normalized text, the matched row's id is carried over along with
its delivery history, and only genuinely new records get a freshly
minted canonical id. That keeps store_id stable -- which matters because
ContextItem.store_id is what ADR-015's retrieval-stats write keys on --
while making the index reproducible from that point forward.

The mode never writes to the live memory.db. Output goes to
memory.db.rebuild alongside it (or --out), so swapping the result in is
always a separate, deliberate step.

--dry-run resolves the same plan without embedding anything and reports
the drift between the vault and the index: how many records match, how
many exist in the vault but not the index, and how many rows are indexed
with no canonical record behind them.

IMPORTANT: Stop the API before running this script. The API caches
indexes in memory, so running a rebuild while the API is live will
cause stale cache issues.

Usage:
    python scripts/rebuild_indexes.py                      # rebuild all
    python scripts/rebuild_indexes.py --type conversation  # one type only
    python scripts/rebuild_indexes.py --skip-sqlite        # JSON indexes only

    python scripts/rebuild_indexes.py --memory-db --dry-run   # drift report
    python scripts/rebuild_indexes.py --memory-db             # -> memory.db.rebuild
    python scripts/rebuild_indexes.py --memory-db --vault PATH --out PATH

Progress is printed every batch so it doesn't look hung on large corpora.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import struct
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.config import get_private_vault_path
from src.memory.authorship import classify_authorship
from src.memory.eval_fixtures import is_eval_fixture, should_index_record
from src.retrieval.embedding_model import embed_texts
from src.retrieval.sqlite_vector_store import SqliteVectorStore

# Batch size for embedding calls — balances speed vs memory
BATCH_SIZE = 50

# Memory types that live in memory.db, each in its own vault directory and
# carrying its own type. Mirrors SQLITE_MEMORY_TYPES in write_memory.py and
# semantic_search.py; kept as a tuple here so enumeration order is stable.
MEMORY_DB_TYPES = ("conversation", "profile", "reflection", "journal")

# Prior-substrate conversation (ADR-015 amendment, "Timestamps and
# prior-substrate conversation"). These records live in memory/ingested/
# with "type": "ingested" on disk, because the reclassification that moved
# them was index-only and left canonical JSON untouched per the append-only
# rule. They are conversation for every retrieval purpose, so the rebuild
# applies the same predicate the migration did rather than reading the
# stale type off disk.
PRIOR_SUBSTRATE_SOURCE = "chatgpt"
PRIOR_SUBSTRATE_ROLES = frozenset({"user", "assistant"})

# Columns carried across a rebuild when a source record can be matched to a
# row in the existing database. Everything here is either delivery history
# or an operator action, and none of it is derivable from canonical records,
# so a rebuild that did not carry it would be lossy in a way the vault
# cannot repair.
#
# authorship is deliberately NOT in this list even though the migration that
# created these rows carried it. It is a pure function of memory_type, source
# and metadata (src/memory/authorship.py), so carrying it would mean a rebuild
# could never correct an authorship defect -- and there is one outstanding:
# the prior-substrate reclassification recomputed the imported assistant turns
# from third_party to mixed, changing their treatment under the relational
# authorship multiplier. Recomputing here keeps the rebuild honest about what
# is derived, and scripts/rebuild_authorship_index.py remains the standalone
# path for the same job.
_CARRIED_COLUMNS = (
    "tier",
    "heat_score",
    "frequency_score",
    "last_retrieved_at",
    "retrieval_count",
    "importance_score",
    "quality",
)


def _normalize_for_join(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _content_key(text: str) -> str:
    """Join key for matching a source record to an existing indexed row.

    Identity in memory.db is not reproducible from the vault: roughly 99% of
    rows carry ids minted by the retired ingest pipeline that wrote
    ingested.db, and those ids appear in no canonical record. Normalized text
    is the only field both sides share, so it is what the rebuild joins on to
    preserve store_id and delivery history. Measured coverage on a real vault
    at the time this was written: 16,804 of 16,899 distinct indexed texts
    matched a canonical record.
    """
    return hashlib.sha256(_normalize_for_join(text).encode("utf-8", "ignore")).hexdigest()


@dataclass
class SourceRecord:
    """One canonical vault record eligible for memory.db."""

    canonical_id: str
    memory_type: str
    text: str
    source: str
    created_at: str
    metadata: dict
    file_path: Path

    @property
    def content_key(self) -> str:
        return _content_key(self.text)


@dataclass
class RebuildPlan:
    """What a rebuild would write, resolved before anything is embedded."""

    matched: list = field(default_factory=list)   # (SourceRecord, existing row dict)
    added: list = field(default_factory=list)     # SourceRecord with no existing row
    orphans: list = field(default_factory=list)   # existing rows with no source record
    collisions: dict = field(default_factory=dict)  # canonical_id -> [SourceRecord, ...]

    @property
    def total(self) -> int:
        return len(self.matched) + len(self.added)

    @property
    def records_lost_to_collision(self) -> int:
        return sum(len(v) - 1 for v in self.collisions.values())

# Memory types that use JSON indexes.
# B-RET-001 retired "conversation" first. The follow-up cleanup retired
# profile, reflection, and journal: src/memory/write_memory.py and
# src/retrieval/semantic_search.py both route those types through
# SQLITE_MEMORY_TYPES, and no live retrieval path reads the per-type
# JSON indexes. The list is intentionally empty - any future memory
# type that uses a JSON index would be added here, but none currently
# do. Stale {type}_index.json files in user vaults are left in place
# (user data); nothing live reads them.
JSON_INDEX_TYPES = []


def rebuild_json_index(vault: Path, memory_type: str) -> int:
    """
    Rebuild a single JSON vector index from canonical vault records.

    Reads all .json files in vault/memory/{memory_type}/, embeds the
    text field from each, and writes a new index file.

    Returns the number of records indexed.
    """
    memory_dir = vault / "memory" / memory_type
    index_path = vault / "embeddings" / f"{memory_type}_index.json"

    if not memory_dir.exists():
        print(f"  [{memory_type}] No memory directory found — skipping")
        return 0

    # Collect all canonical records
    files = sorted(memory_dir.glob("*.json"))
    if not files:
        print(f"  [{memory_type}] No records found — writing empty index")
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("[]", encoding="utf-8")
        return 0

    print(f"  [{memory_type}] {len(files)} canonical records to index")

    records = []
    texts = []
    skipped = 0

    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            text = data.get("text", "")
            if not text or not text.strip():
                skipped += 1
                continue
            records.append((f, data))
            texts.append(text)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  [{memory_type}] Error reading {f.name}: {e}")
            skipped += 1

    if not texts:
        print(f"  [{memory_type}] No valid texts — writing empty index")
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text("[]", encoding="utf-8")
        return 0

    # Embed in batches
    all_embeddings = []
    total = len(texts)
    for i in range(0, total, BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        embeddings = embed_texts(batch)
        all_embeddings.extend(embeddings)
        done = min(i + BATCH_SIZE, total)
        pct = done / total * 100
        print(f"  [{memory_type}] {done}/{total} embedded ({pct:.0f}%)")

    # Build index entries
    index_data = []
    for (f, data), embedding in zip(records, all_embeddings):
        text = data.get("text", "")
        normalized = text.lower().strip()
        import re
        normalized = re.sub(r"\s+", " ", normalized)

        index_data.append({
            "id": data.get("id", f.stem),
            "timestamp": data.get("timestamp", ""),
            "type": data.get("type", memory_type),
            "text": text,
            "normalized_text": normalized,
            "source": data.get("source", ""),
            "tags": data.get("tags", []),
            "file_path": str(f),
            "embedding": embedding,
            "metadata": data.get("metadata", {}),
        })

    # Write index
    index_path.parent.mkdir(parents=True, exist_ok=True)
    with index_path.open("w", encoding="utf-8") as fh:
        json.dump(index_data, fh, ensure_ascii=False)

    size_mb = index_path.stat().st_size / (1024 * 1024)
    if skipped:
        print(f"  [{memory_type}] Done: {len(index_data)} indexed, {skipped} skipped ({size_mb:.1f} MB)")
    else:
        print(f"  [{memory_type}] Done: {len(index_data)} indexed ({size_mb:.1f} MB)")

    return len(index_data)


def rebuild_sqlite_index(vault: Path) -> int:
    """
    Rebuild embeddings in the ingested SQLite vector store.

    Reads each row's text, re-embeds it, and updates the embedding BLOB
    in place. Does not change any other fields.

    Returns the number of records updated.
    """
    db_path = vault / "embeddings" / "ingested.db"
    if not db_path.exists():
        print("  [ingested] No ingested.db found — skipping")
        return 0

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    total = conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0]
    print(f"  [ingested] {total} records to re-embed")

    if total == 0:
        conn.close()
        return 0

    # Read all ids and texts
    cursor = conn.execute("SELECT id, text FROM vectors")
    rows = cursor.fetchall()

    updated = 0
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i:i + BATCH_SIZE]
        texts = [row["text"] for row in batch]
        ids = [row["id"] for row in batch]

        embeddings = embed_texts(texts)

        for record_id, embedding in zip(ids, embeddings):
            n = len(embedding)
            blob = struct.pack(f"{n}f", *embedding)
            conn.execute(
                "UPDATE vectors SET embedding = ? WHERE id = ?",
                (blob, record_id),
            )

        conn.commit()
        updated += len(batch)
        done = min(i + BATCH_SIZE, len(rows))
        pct = done / len(rows) * 100
        print(f"  [ingested] {done}/{len(rows)} re-embedded ({pct:.0f}%)")

    conn.close()
    print(f"  [ingested] Done: {updated} records updated")
    return updated


def _is_prior_substrate(record: dict) -> bool:
    """True when an ingested chunk is prior-substrate conversation.

    Same predicate as scripts/reclassify_prior_substrate_conversation.py:
    source is the chat export, and the role resolves to exactly user or
    assistant. A chunk whose role is missing or unrecognised is left alone
    rather than guessed at, which is why it stays out of memory.db.
    """
    if (record.get("source") or "") != PRIOR_SUBSTRATE_SOURCE:
        return False
    role = ((record.get("metadata") or {}).get("role") or "")
    return role in PRIOR_SUBSTRATE_ROLES


def collect_source_records(vault: Path) -> list[SourceRecord]:
    """Enumerate every canonical vault record that belongs in memory.db.

    Two groups. The four native types take their directory's type and their
    record id. Prior-substrate chunks take type conversation and their
    chunk_id, which is the filename stem -- ingested payloads carry the text
    under "content" rather than "text" and have no "id" field at all
    (src/ingest/writers.py:26-35).
    """
    records: list[SourceRecord] = []
    skipped_fixtures: list[str] = []

    for memory_type in MEMORY_DB_TYPES:
        directory = vault / "memory" / memory_type
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                print(f"  [memory.db] Unreadable {memory_type} record {path.name}: {exc}")
                continue
            text = data.get("text") or ""
            if not text.strip():
                continue
            # Eval fixtures index only into the configured test vault, so a
            # rebuild cannot reintroduce what the write path now refuses
            # (issue #211). Same helper, same fail-closed direction.
            if not should_index_record(vault, data.get("source"), data.get("metadata")):
                skipped_fixtures.append(path.stem)
                continue
            records.append(
                SourceRecord(
                    canonical_id=str(data.get("id") or path.stem),
                    memory_type=memory_type,
                    text=text,
                    source=str(data.get("source") or ""),
                    created_at=str(data.get("timestamp") or data.get("created_at") or ""),
                    metadata=data.get("metadata") or {},
                    file_path=path,
                )
            )

    ingested_dir = vault / "memory" / "ingested"
    if ingested_dir.exists():
        for path in sorted(ingested_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                print(f"  [memory.db] Unreadable ingested chunk {path.name}: {exc}")
                continue
            if not _is_prior_substrate(data):
                continue
            text = data.get("content") or data.get("text") or ""
            if not text.strip():
                continue
            records.append(
                SourceRecord(
                    canonical_id=str(data.get("chunk_id") or path.stem),
                    memory_type="conversation",
                    text=text,
                    source=str(data.get("source") or ""),
                    created_at=str(data.get("created_at") or ""),
                    metadata=data.get("metadata") or {},
                    file_path=path,
                )
            )

    if skipped_fixtures:
        print(
            f"  [memory.db] skipped {len(skipped_fixtures)} eval fixture(s): "
            "not indexing test corpus into a non-test vault"
        )

    return records


def _read_existing_rows(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(vectors)")}
        wanted = ["id", "text"] + [c for c in _CARRIED_COLUMNS if c in columns]
        rows = conn.execute(f"SELECT {', '.join(wanted)} FROM vectors").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def plan_memory_db_rebuild(vault: Path, existing_db: Path) -> RebuildPlan:
    """Resolve source records against an existing index without writing.

    Matching is by normalized text. Where several records share a text, both
    sides are ordered by id and paired positionally, so the pairing is
    deterministic across runs rather than dependent on filesystem or SQLite
    ordering. Surplus on the source side becomes an addition; surplus on the
    index side becomes an orphan.
    """
    sources = collect_source_records(vault)
    existing = _read_existing_rows(existing_db)

    by_key_source: dict[str, list[SourceRecord]] = defaultdict(list)
    for record in sources:
        by_key_source[record.content_key].append(record)
    for bucket in by_key_source.values():
        bucket.sort(key=lambda r: r.canonical_id)

    by_key_existing: dict[str, list[dict]] = defaultdict(list)
    for row in existing:
        by_key_existing[_content_key(row.get("text", ""))].append(row)
    for bucket in by_key_existing.values():
        bucket.sort(key=lambda r: str(r.get("id") or ""))

    plan = RebuildPlan()

    for key, bucket in by_key_source.items():
        counterparts = by_key_existing.get(key, [])
        for index, record in enumerate(bucket):
            if index < len(counterparts):
                plan.matched.append((record, counterparts[index]))
            else:
                plan.added.append(record)

    for key, bucket in by_key_existing.items():
        surplus = len(bucket) - len(by_key_source.get(key, []))
        if surplus > 0:
            plan.orphans.extend(bucket[-surplus:])

    # Canonical ids are not guaranteed unique across memory types.
    # write_memory derives memory_id from the timestamp alone
    # (src/memory/write_memory.py) while the directory carries the type, so a
    # conversation and a reflection written in the same second share an id --
    # and memory.db keys on id, so the second write silently replaces the
    # first. Detect it here rather than reproduce the loss: a rebuild whose
    # output is quietly smaller than its input is exactly the failure this
    # mode exists to rule out.
    by_canonical: dict[str, list[SourceRecord]] = defaultdict(list)
    for record in sources:
        by_canonical[record.canonical_id].append(record)
    plan.collisions = {
        canonical_id: sorted(bucket, key=lambda r: (r.memory_type, str(r.file_path)))
        for canonical_id, bucket in by_canonical.items()
        if len(bucket) > 1
    }

    return plan


def _row_for(record: SourceRecord, existing: dict | None) -> dict:
    """Build the insert payload for one source record.

    store_id comes from the matched row when there is one, so delivery
    history keyed on it survives; otherwise the vault-canonical id is minted.
    Derived fields are recomputed from the source record rather than carried,
    except those in _CARRIED_COLUMNS which the vault cannot reproduce.
    """
    row = {
        "id": str(existing["id"]) if existing else record.canonical_id,
        "text": record.text,
        "source": record.source,
        "memory_type": record.memory_type,
        "created_at": record.created_at,
        "authorship": classify_authorship(record.memory_type, record.source, record.metadata),
        "metadata": {
            **record.metadata,
            "file_path": str(record.file_path),
            "normalized_text": _normalize_for_join(record.text),
        },
    }
    if existing:
        for column in _CARRIED_COLUMNS:
            value = existing.get(column)
            if value is not None:
                row[column] = value
    return row


def _print_plan(plan: RebuildPlan, existing_count: int) -> None:
    print(f"  [memory.db] existing rows          : {existing_count}")
    print(f"  [memory.db] source records eligible: {plan.total}")
    print(f"  [memory.db]   matched (id preserved): {len(plan.matched)}")
    print(f"  [memory.db]   added   (id minted)   : {len(plan.added)}")
    print(f"  [memory.db]   orphaned in index     : {len(plan.orphans)}")
    if plan.added:
        by_type: dict[str, int] = defaultdict(int)
        for record in plan.added:
            by_type[record.memory_type] += 1
        print(f"  [memory.db]   additions by type    : {dict(sorted(by_type.items()))}")


class IdCollisionError(RuntimeError):
    """Two or more canonical records claim the same id."""


def rebuild_memory_db(
    vault: Path,
    out_path: Path | None = None,
    dry_run: bool = False,
    keep_orphans: bool = False,
    allow_id_collisions: bool = False,
) -> int:
    """Rebuild memory.db from canonical vault records.

    Never writes to the live database. Output goes to out_path, defaulting to
    memory.db.rebuild alongside the original, so swapping the result in stays
    a deliberate separate step.

    Returns the number of records written (or that would be written).
    """
    existing_db = vault / "embeddings" / "memory.db"
    target = out_path or (vault / "embeddings" / "memory.db.rebuild")

    plan = plan_memory_db_rebuild(vault, existing_db)
    existing_count = len(_read_existing_rows(existing_db))
    _print_plan(plan, existing_count)

    if plan.orphans and not keep_orphans:
        print(
            f"  [memory.db] dropping {len(plan.orphans)} orphaned row(s) with no "
            "canonical record -- pass --keep-orphans to retain them"
        )

    if plan.collisions:
        lost = plan.records_lost_to_collision
        print(
            f"  [memory.db] {len(plan.collisions)} canonical id(s) claimed by more "
            f"than one record; {lost} record(s) would be lost"
        )
        for canonical_id, bucket in sorted(plan.collisions.items()):
            types = ", ".join(sorted({r.memory_type for r in bucket}))
            print(f"  [memory.db]   id shared across types: {types}")
        if not allow_id_collisions:
            raise IdCollisionError(
                f"{lost} canonical record(s) would be silently dropped because their "
                f"id is shared. Fix the colliding records, or pass "
                f"--allow-id-collisions to proceed and accept the loss."
            )

    if dry_run:
        print("  [memory.db] dry run: nothing embedded, nothing written")
        return plan.total

    pairs = [(record, existing) for record, existing in plan.matched]
    pairs += [(record, None) for record in plan.added]

    if target.exists():
        target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteVectorStore(target)

    total = len(pairs)
    written = 0
    carried: list[tuple[str, dict]] = []
    for start in range(0, total, BATCH_SIZE):
        batch = pairs[start:start + BATCH_SIZE]
        embeddings = embed_texts([record.text for record, _ in batch])
        for (record, existing), embedding in zip(batch, embeddings):
            row = _row_for(record, existing)
            row["embedding"] = embedding
            store.insert(row)
            # SqliteVectorStore.insert writes only its own fixed column set
            # (id, text, embedding, source, memory_type, created_at,
            # metadata, authorship). Delivery history has to be applied
            # separately or it silently falls back to schema defaults --
            # heat_score would read 1.0 on a corpus whose real value is
            # near zero, which would hand every rebuilt record a hot tier
            # on the next nightly pass.
            history = {c: row[c] for c in _CARRIED_COLUMNS if c in row}
            if history:
                carried.append((row["id"], history))
            written += 1
        done = min(start + BATCH_SIZE, total)
        print(f"  [memory.db] {done}/{total} embedded ({done / total * 100:.0f}%)")

    _apply_carried_columns(target, carried)

    if keep_orphans:
        for row in plan.orphans:
            print("  [memory.db] retaining orphan row (no canonical record)")
        written += _carry_orphans(existing_db, target, plan.orphans)

    print(f"  [memory.db] Done: {written} records written to {target.name}")
    if plan.added:
        # Newly indexed records take the tier column's schema default of
        # 'hot'. TieringService owns tier assignment and will settle them on
        # its next pass; computing a tier here would make this a second
        # authority for the same value. Until that pass runs, those records
        # rank at full weight rather than their eventual one.
        print(
            f"  [memory.db] {len(plan.added)} new record(s) start at the default "
            "tier; run TieringService after swapping this file in"
        )
    return written


def _apply_carried_columns(target: Path, carried: list[tuple[str, dict]]) -> None:
    """Write the delivery-history columns that SqliteVectorStore.insert skips."""
    if not carried:
        return
    conn = sqlite3.connect(str(target))
    try:
        available = {r[1] for r in conn.execute("PRAGMA table_info(vectors)")}
        for record_id, history in carried:
            fields = {k: v for k, v in history.items() if k in available}
            if not fields:
                continue
            assignments = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE vectors SET {assignments} WHERE id = ?",
                (*fields.values(), record_id),
            )
        conn.commit()
    finally:
        conn.close()


def _carry_orphans(existing_db: Path, target: Path, orphans: list[dict]) -> int:
    """Copy orphaned rows verbatim, embeddings included, into the rebuild.

    Orphans have no canonical record, so there is nothing to re-embed from;
    the stored embedding is the only copy that exists.
    """
    if not orphans:
        return 0
    ids = [str(row["id"]) for row in orphans]
    source = sqlite3.connect(f"file:{existing_db}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    destination = sqlite3.connect(str(target))
    try:
        columns = [r[1] for r in destination.execute("PRAGMA table_info(vectors)")]
        placeholders = ", ".join("?" for _ in columns)
        copied = 0
        for record_id in ids:
            row = source.execute(
                f"SELECT {', '.join(columns)} FROM vectors WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                continue
            destination.execute(
                f"INSERT OR REPLACE INTO vectors ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(row),
            )
            copied += 1
        destination.commit()
        return copied
    finally:
        source.close()
        destination.close()


def main():
    sys.stdout.reconfigure(encoding="utf-8")

    import argparse
    parser = argparse.ArgumentParser(description="Rebuild Ember-2 vector indexes")
    parser.add_argument("--type", help="Rebuild only this memory type")
    parser.add_argument("--skip-sqlite", action="store_true", help="Skip SQLite ingested index")
    parser.add_argument(
        "--memory-db",
        action="store_true",
        help="Rebuild memory.db from canonical vault records (writes to memory.db.rebuild)",
    )
    parser.add_argument("--out", help="Output path for --memory-db (default: memory.db.rebuild)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --memory-db: report the drift between source and index, write nothing",
    )
    parser.add_argument(
        "--keep-orphans",
        action="store_true",
        help="With --memory-db: retain indexed rows that have no canonical record",
    )
    parser.add_argument(
        "--allow-id-collisions",
        action="store_true",
        help="With --memory-db: proceed even though records sharing an id will be lost",
    )
    parser.add_argument("--vault", help="Override the vault path (verification against a copy)")
    args = parser.parse_args()

    vault = Path(args.vault) if args.vault else get_private_vault_path()
    print(f"Vault: {vault}")
    print(f"Embedding model: {__import__('src.core.config', fromlist=['get_ember_embed_model']).get_ember_embed_model()}")
    print()

    start = time.time()
    total_records = 0

    # memory.db is its own mode. It is the only index whose identity is not
    # reproducible from the vault, so it never overwrites the live file and
    # is not folded into the default "rebuild everything" path.
    if args.memory_db:
        try:
            total_records += rebuild_memory_db(
                vault,
                out_path=Path(args.out) if args.out else None,
                dry_run=args.dry_run,
                keep_orphans=args.keep_orphans,
                allow_id_collisions=args.allow_id_collisions,
            )
        except IdCollisionError as exc:
            print()
            print(f"ABORTED: {exc}")
            sys.exit(2)
        elapsed = time.time() - start
        print()
        print(f"{'=' * 50}")
        print(f"  memory.db rebuild {'planned' if args.dry_run else 'complete'}")
        print(f"  Total records: {total_records}")
        print(f"  Time: {elapsed:.1f}s ({elapsed / 60:.1f} minutes)")
        print(f"{'=' * 50}")
        return

    # Rebuild JSON indexes
    types_to_rebuild = [args.type] if args.type else JSON_INDEX_TYPES
    for memory_type in types_to_rebuild:
        if memory_type == "ingested":
            continue  # handled by SQLite
        total_records += rebuild_json_index(vault, memory_type)

    # Rebuild SQLite index
    if not args.skip_sqlite and args.type in (None, "ingested"):
        print()
        total_records += rebuild_sqlite_index(vault)

    elapsed = time.time() - start
    print()
    print(f"{'=' * 50}")
    print(f"  Rebuild complete")
    print(f"  Total records: {total_records}")
    print(f"  Time: {elapsed:.1f}s ({elapsed/60:.1f} minutes)")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
