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
import sqlite3
import struct
from pathlib import Path


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
        where the module-level singleton may be accessed from multiple
        request handler threads.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._create_table()

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

    def insert(self, record: dict) -> None:
        """
        Insert a record into the vector store.

        The embedding (list[float]) is packed into a binary BLOB using
        struct.pack with format '{n}f'. Metadata (dict) is serialised to
        a JSON string. If a record with the same id already exists the
        insert is silently skipped (INSERT OR IGNORE).

        Required keys: id, text, embedding
        Optional keys: source, memory_type, created_at, metadata
        """
        embedding: list[float] = record["embedding"]
        n = len(embedding)
        embedding_blob = struct.pack(f"{n}f", *embedding)

        metadata = record.get("metadata", {})
        metadata_str = json.dumps(metadata, ensure_ascii=False)

        self._conn.execute(
            """
            INSERT OR IGNORE INTO vectors
                (id, text, embedding, source, memory_type, created_at, metadata)
            VALUES
                (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["id"],
                record["text"],
                embedding_blob,
                record.get("source"),
                record.get("memory_type"),
                record.get("created_at"),
                metadata_str,
            ),
        )
        self._conn.commit()

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

        Each result dict matches the format returned by VectorIndex.search():
            {
                "content":     str,
                "score":       float,
                "path":        str | None,
                "memory_type": str | None,
                "metadata":    dict,
            }
        """
        if memory_type:
            cursor = self._conn.execute(
                "SELECT * FROM vectors WHERE memory_type = ?",
                (memory_type,),
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

            results.append(
                {
                    "content": row["text"],
                    "score": score,
                    "path": metadata.get("file_path"),
                    "memory_type": row["memory_type"],
                    "metadata": metadata,
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

    def close(self) -> None:
        """Close the SQLite connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
