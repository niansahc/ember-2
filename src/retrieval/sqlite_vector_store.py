"""
src/retrieval/sqlite_vector_store.py

SQLite-backed vector store for Ember-2.

Replaces the single-file JSON index for ingested content, which grew to
1.32 GB and became unloadable within the 50 MB size guard. SQLite gives
us partial reads, row-level inserts, and a stable binary format without
any external dependencies.

Embeddings are stored as binary BLOBs using struct.pack (format: '{n}f'),
which is compact and fast to deserialise. Metadata is stored as a JSON
string and parsed back on retrieval.

Usage
-----
from src.retrieval.sqlite_vector_store import SqliteVectorStore
from pathlib import Path

store = SqliteVectorStore(Path("private_vault/embeddings/ingested.db"))
store.insert({
    "id": "chunk_001",
    "text": "Some chunk of ingested content",
    "embedding": [0.01, 0.02, ...],
    "source": "chatgpt_export",
    "memory_type": "ingested",
    "created_at": "2026-03-21T10-00-00",
    "metadata": {"doc_id": "doc_001", "role": "user"},
})
results = store.search(query_embedding, limit=5)
store.close()
"""

from __future__ import annotations

import json
import logging
import math
import re
import sqlite3
import struct
from datetime import datetime
from pathlib import Path

from src.core.timestamps import parse_vault_timestamp

logger = logging.getLogger(__name__)

# Separator between a colliding record's id and its disambiguating counter.
# See SqliteVectorStore.insert (issue #210).
ID_COLLISION_MARKER = "#"


def _same_text(left: str | None, right: str | None) -> bool:
    """Whitespace- and case-insensitive equality, matching the rebuild's join.

    Re-indexing the same record must not be mistaken for a collision just
    because the text was re-wrapped on the way through.
    """
    def _norm(value: str | None) -> str:
        return re.sub(r"\s+", " ", (value or "").strip().lower())

    return _norm(left) == _norm(right)


class SqliteVectorStore:
    """
    SQLite-backed vector store for embedding search.

    Schema
    ------
    vectors(
        id          TEXT PRIMARY KEY,
        text        TEXT NOT NULL,
        embedding   BLOB NOT NULL,     -- struct-packed list[float]
        source      TEXT,
        memory_type TEXT,
        created_at  TEXT,
        metadata    TEXT               -- JSON string
    )
    """

    def __init__(self, db_path: Path) -> None:
        """
        Open (or create) the SQLite database at db_path.

        Creates the vectors table if it does not already exist.
        check_same_thread=False is required for FastAPI compatibility,
        where a cached store may be accessed from multiple request
        handler threads.

        db_path is retained, resolved, as a public attribute. The vault
        swap endpoint reads it to confirm that every store it can reach
        belongs to the vault that was just activated.
        """
        db_path = Path(db_path).resolve()
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_table()
        self._migrate_tiering_columns()
        self._migrate_authorship_column()
        self._migrate_quality_column()
        self._has_quality_column = self._check_column_exists("quality")
        self._has_authorship_column = self._check_column_exists("authorship")

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _create_table(self) -> None:
        """Create the vectors table if it does not already exist."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS vectors (
                id          TEXT PRIMARY KEY,
                text        TEXT NOT NULL,
                embedding   BLOB NOT NULL,
                source      TEXT,
                memory_type TEXT,
                created_at  TEXT,
                metadata    TEXT
            )
            """
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def insert(self, record: dict) -> str:
        """
        Insert a record into the vector store. Returns the id actually written.

        The embedding (list[float]) is packed into a binary BLOB using
        struct.pack with format '{n}f'. Metadata (dict) is serialised to
        a JSON string.

        Required keys: id, text, embedding
        Optional keys: source, memory_type, created_at, metadata, authorship

        authorship defaults to 'unknown' (the column default) when absent,
        matching the read path's fallback. _migrate_authorship_column()
        runs in __init__ before any insert() call, so the column always
        exists by the time this executes.

        Id collisions (issue #210)
        --------------------------
        This used to be INSERT OR IGNORE, which made two different records
        claiming one id indistinguishable from a re-index of the same
        record: the second was dropped and nothing said so. Ids come from
        `write_memory`, which derives them from the timestamp alone while
        the directory carries the type, so a conversation and a reflection
        written in the same second shared an id -- and the reflection was
        never indexed, so it was unreachable by retrieval while its
        canonical file sat in the vault looking fine.

        Now the two cases are separated:

          - same id, same normalized text -> a re-index of the same record.
            Skipped, as before, and the existing id is returned. Rebuilds
            and backfills depend on this being idempotent.
          - same id, different text -> a real collision. The record is
            written under a disambiguated id (`{id}#2`, `{id}#3`, ...) and
            logged at WARNING. Nothing is discarded.

        Disambiguating rather than raising is deliberate on the live write
        path: the canonical JSON file is already on disk by the time
        write_memory calls this, so raising would leave a record that
        exists but cannot be retrieved -- the exact failure being fixed.
        The suffix only ever changes the index's primary key; the canonical
        record keeps the id it was written with, and vectors.id has never
        been required to equal it (roughly 99 percent of rows carry ids
        minted by the retired ingest pipeline).
        """
        embedding: list[float] = record["embedding"]
        n = len(embedding)
        embedding_blob = struct.pack(f"{n}f", *embedding)

        metadata = record.get("metadata", {})
        metadata_str = json.dumps(metadata, ensure_ascii=False)

        record_id = str(record["id"])
        text = record["text"]

        existing = self._conn.execute(
            "SELECT text FROM vectors WHERE id = ?", (record_id,)
        ).fetchone()
        if existing is not None:
            if _same_text(existing["text"], text):
                return record_id  # idempotent re-index, nothing to do
            record_id = self._disambiguate_id(record_id)
            logger.warning(
                "[INDEX] id collision: a %s record claimed an id already held by a "
                "different record; indexing it under a disambiguated id instead of "
                "discarding it (issue #210)",
                record.get("memory_type") or "unknown-type",
            )

        self._conn.execute(
            """
            INSERT INTO vectors
                (id, text, embedding, source, memory_type, created_at, metadata, authorship)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                text,
                embedding_blob,
                record.get("source"),
                record.get("memory_type"),
                record.get("created_at"),
                metadata_str,
                record.get("authorship", "unknown"),
            ),
        )
        self._conn.commit()
        return record_id

    def _disambiguate_id(self, record_id: str) -> str:
        """First free `{record_id}#n`, n starting at 2.

        '#' cannot appear in a vault timestamp id or in an ingest chunk id,
        so a disambiguated id is recognisable as one and cannot be produced
        by the id minters themselves.
        """
        suffix = 2
        while self._conn.execute(
            "SELECT 1 FROM vectors WHERE id = ?", (f"{record_id}{ID_COLLISION_MARKER}{suffix}",)
        ).fetchone() is not None:
            suffix += 1
        return f"{record_id}{ID_COLLISION_MARKER}{suffix}"

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query_embedding: list[float],
        limit: int = 5,
        memory_type: str | None = None,
    ) -> list[dict]:
        """
        Cosine similarity search over all stored vectors.

        Iterates every row, unpacks the embedding BLOB, computes cosine
        similarity against query_embedding, and returns the top `limit`
        results sorted by descending score.

        If memory_type is provided, only rows matching that type are
        searched.

        Each result dict matches the format returned by VectorIndex.search(),
        plus "id":
            {
                "id":          str,   # the row's primary key -- see note below
                "content":     str,
                "score":       float,
                "path":        str | None,
                "memory_type": str | None,
                "metadata":    dict,
            }

        "id" is the actual `vectors.id` primary key (ADR-015 amendment,
        implementation step 4). Callers previously had no way to address
        this row again -- ContextItem.id (populated from metadata.get(
        "chunk_id", path)) is a different identifier for a different job
        (session-scoped hedge tracking) and does not match this column, so
        update_retrieval_stats() calls keyed off it matched zero rows. See
        ContextItem.store_id.
        """
        quality_filter = " AND (quality IS NULL OR quality != 'suppressed')" if self._has_quality_column else ""

        if memory_type:
            cursor = self._conn.execute(
                f"SELECT * FROM vectors WHERE memory_type = ?{quality_filter}",
                (memory_type,),
            )
        else:
            if self._has_quality_column:
                cursor = self._conn.execute(
                    "SELECT * FROM vectors WHERE quality IS NULL OR quality != 'suppressed'"
                )
            else:
                cursor = self._conn.execute("SELECT * FROM vectors")

        scored: list[tuple[float, sqlite3.Row]] = []

        for row in cursor:
            embedding = self._unpack_embedding(row["embedding"])
            score = self._cosine_similarity(query_embedding, embedding)
            scored.append((score, row))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, row in scored[:limit]:
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (json.JSONDecodeError, TypeError):
                metadata = {}

            # Include tier if the column exists (ADR-015)
            tier = "hot"
            try:
                tier = row["tier"] or "hot"
            except (IndexError, KeyError):
                pass

            # Include authorship if the column exists.
            # Falls back to 'unknown' to match the column default so callers
            # don't need a None-branch when comparing against the scoring map.
            authorship = "unknown"
            try:
                authorship = row["authorship"] or "unknown"
            except (IndexError, KeyError):
                pass

            results.append(
                {
                    "id": row["id"],
                    "content": row["text"],
                    "score": score,
                    "path": metadata.get("file_path"),
                    "memory_type": row["memory_type"],
                    "metadata": metadata,
                    "tier": tier,
                    "authorship": authorship,
                    # B-RET-002: surface the column-level created_at so
                    # ContextItem.timestamp populates correctly. The JSON
                    # metadata blob does not carry this field
                    # (write_memory.py composes metadata without it), so
                    # callers were reading None from metadata.get and
                    # per-item age labels never rendered.
                    "created_at": row["created_at"],
                    # ADR-021 amendment 2026-04-24: surface the cached
                    # embedding so the T2 pattern detector can read it
                    # from ContextItem.metadata without recomputation.
                    "embedding": embedding,
                }
            )

        return results

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Return the total number of records in the store."""
        row = self._conn.execute("SELECT COUNT(*) FROM vectors").fetchone()
        return row[0]

    def delete_by_ids(self, ids: list[str]) -> int:
        """Delete rows by primary key. Returns the count of rows removed.

        Used by maintenance tools (tools/suppress_reflections.py) that
        need to remove specific records from the vector index while the
        canonical JSON records remain on disk with their suppression
        flag. The canonical record is the source of truth; the vector
        store is rebuildable from canonical records.
        """
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cursor = self._conn.execute(
            f"DELETE FROM vectors WHERE id IN ({placeholders})",
            ids,
        )
        self._conn.commit()
        return cursor.rowcount

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _migrate_tiering_columns(self) -> None:
        """Add tiering columns if they don't exist (ADR-015)."""
        import sqlite3 as _sqlite3
        for col_def in [
            "ALTER TABLE vectors ADD COLUMN tier TEXT DEFAULT 'hot'",
            "ALTER TABLE vectors ADD COLUMN last_retrieved_at TEXT",
            # retrieval_count and importance_score are legacy (ADR-015
            # amendment, implementation step 4): retrieval_count was a
            # monotonic counter that installed a permanent floor once a
            # record was retrieved four times, and importance_score's
            # heuristic-by-type ladder never contributed to heat (max
            # contribution 0.2 was exactly the warm threshold). Neither
            # column is dropped -- SQLite DROP COLUMN support is
            # version-dependent and nothing reads either column anymore,
            # so they are inert rather than removed. frequency_score
            # replaces retrieval_count as a decayed accumulator; see
            # update_retrieval_stats().
            "ALTER TABLE vectors ADD COLUMN retrieval_count INTEGER DEFAULT 0",
            "ALTER TABLE vectors ADD COLUMN importance_score REAL DEFAULT 0.5",
            "ALTER TABLE vectors ADD COLUMN frequency_score REAL DEFAULT 0.0",
            "ALTER TABLE vectors ADD COLUMN heat_score REAL DEFAULT 1.0",
        ]:
            try:
                self._conn.execute(col_def)
            except _sqlite3.OperationalError:
                pass  # column already exists
        self._conn.commit()

    def _migrate_authorship_column(self) -> None:
        """Add authorship column for identity-query retrieval filtering.

        Schema migration. Vault JSON records are
        never mutated (append-only rule, CLAUDE.md §3); the authorship
        value is an index-level derived fact, rebuildable from source and
        role signals via scripts/rebuild_authorship_index.py.

        Values: 'first_person', 'third_party', 'mixed', 'unknown' (default).
        """
        import sqlite3 as _sqlite3
        try:
            self._conn.execute(
                "ALTER TABLE vectors ADD COLUMN authorship TEXT DEFAULT 'unknown'"
            )
        except _sqlite3.OperationalError:
            pass  # column already exists
        self._conn.commit()

    def _migrate_quality_column(self) -> None:
        """Add the quality column for suppression filtering.

        Previously added outside committed code (ad hoc, ingested.db
        only) -- the reclassify_prior_substrate_conversation.py migration
        (ADR-015 amendment step 5) is the first committed writer of
        quality-flagged rows into memory.db, and those rows are now a
        permanent part of that database. Self-migrating here, matching
        _migrate_tiering_columns/_migrate_authorship_column, means any
        future SqliteVectorStore(memory_db) -- a rebuilt vault, a test
        fixture, a different machine -- gets the column the same way it
        already gets tier/authorship, instead of depending on a one-off
        script having run first.

        Values: 'ok' (default-equivalent), 'suppressed'. NULL (unset) is
        treated the same as 'ok' by search()'s quality_filter.
        """
        import sqlite3 as _sqlite3
        try:
            self._conn.execute("ALTER TABLE vectors ADD COLUMN quality TEXT")
        except _sqlite3.OperationalError:
            pass  # column already exists
        self._conn.commit()

    def update_retrieval_stats(self, record_ids: list[str]) -> None:
        """
        Decay-then-increment frequency_score and set last_retrieved_at for
        selected records (ADR-015 amendment, implementation step 4).

        Called after final context packet assembly — only records that were
        actually selected for the prompt get their stats updated.

        frequency_score is a decayed accumulator, not a monotonic count.
        Each retrieval decays the existing value by the time elapsed since
        it was last touched (same halflife as recency, so the two terms
        share one decay curve per the ADR), then adds 1:

            new_frequency = old_frequency * 2^(-elapsed_days/halflife) + 1

        This can't be a single batched SQL statement (SQLite has no
        builtin exponentiation reachable from parameterized SQL), so this
        does a per-record read-then-write. Acceptable: record_ids here is
        always the small, bounded set selected into one context packet,
        never a corpus-wide scan.

        A record retrieved regularly (within one halflife of the last
        retrieval each time) approaches a bounded steady-state frequency
        rather than growing without limit, and one that stops being
        retrieved decays back toward 0 like recency does -- unlike the
        old retrieval_count, which could only ever go up, permanently
        flooring any record retrieved 4+ times at warm-or-hotter.
        """
        if not record_ids:
            return

        # Last line of defense for read-only replay. build_context's
        # read_only flag is a per-call opt-in and the ablation harness did
        # not pass it; gating here covers every entry point, including any
        # that calls this store directly. See src/retrieval/retrieval_stats.py.
        from src.retrieval.retrieval_stats import retrieval_stats_disabled_now

        if retrieval_stats_disabled_now():
            logger.info(
                "[TIERING] retrieval-stat write suppressed for %d record(s): "
                "read-only mode is active",
                len(record_ids),
            )
            return

        from src.core.config import get_tier_recency_halflife_days

        halflife = get_tier_recency_halflife_days()
        now_dt = datetime.now()
        now_str = now_dt.strftime("%Y-%m-%dT%H-%M-%S")

        for record_id in record_ids:
            row = self._conn.execute(
                "SELECT frequency_score, last_retrieved_at, created_at "
                "FROM vectors WHERE id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                continue

            old_frequency = row["frequency_score"] or 0.0
            reference = row["last_retrieved_at"] or row["created_at"]
            ref_dt = parse_vault_timestamp(reference) if reference else None

            if ref_dt is None or halflife <= 0:
                decay = 0.0
            else:
                elapsed_days = max((now_dt.replace(tzinfo=ref_dt.tzinfo) - ref_dt).days, 0)
                decay = math.pow(2, -elapsed_days / halflife)

            new_frequency = old_frequency * decay + 1.0

            self._conn.execute(
                """
                UPDATE vectors
                SET frequency_score = ?,
                    last_retrieved_at = ?
                WHERE id = ?
                """,
                (new_frequency, now_str, record_id),
            )
        self._conn.commit()

    def _check_column_exists(self, column_name: str) -> bool:
        """Check if a column exists in the vectors table."""
        cursor = self._conn.execute("PRAGMA table_info(vectors)")
        columns = {row["name"] for row in cursor}
        return column_name in columns

    def _unpack_embedding(self, blob: bytes) -> list[float]:
        """
        Unpack a struct-packed BLOB back into a list of floats.

        Format: '{n}f' where n = len(blob) // 4 (each float is 4 bytes).
        """
        n = len(blob) // 4
        return list(struct.unpack(f"{n}f", blob))

    def _cosine_similarity(self, a: list[float], b: list[float]) -> float:
        """
        Cosine similarity between two vectors.

        Returns 0.0 if either vector is empty, mismatched in length,
        or has zero magnitude — matching VectorIndex behaviour.
        """
        if not a or not b or len(a) != len(b):
            return 0.0

        dot_product = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0

        return dot_product / (norm_a * norm_b)
