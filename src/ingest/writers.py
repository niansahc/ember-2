"""
src/ingest/writers.py

Write ingested chunks to the vault and index them where retrieval reads.

Each chunk is written as a canonical JSON file under memory/ingested/ and,
unless ADR-033 keeps it out, embedded into the SQLite store that
src/ingest/index_routing.py names for it: ingested.db for documents,
memory.db as `conversation` for a ChatGPT export (ADR-015). Issue #174:
this used to save every embedding to ingested_index.json, which nothing
reads, so ingested documents were stored but never retrievable.

index_chunks() is the indexing half on its own, for chunk files already in
the vault (scripts/backfill_ingested_index.py). It and the live writer
share one routine (_IndexRun).
"""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from pathlib import Path

from src.core.jsonio import safe_write_json
from src.ingest.index_routing import index_target, is_indexable
from src.memory.authorship import classify_authorship
from src.retrieval.embed_memory import embed_text
from src.retrieval.sqlite_vector_store import IndexOutcome, content_key, read_index_snapshot
from src.retrieval.store_cache import get_store


logger = logging.getLogger("ember.ingest.writers")

# Overlapping ingests of one document write the same chunk paths, and on
# Windows os.replace() fails while another thread replaces that file. Held for
# one file write at a time, never across embedding.
_chunk_file_lock = threading.Lock()

# Document-level metadata the ChatGPT importer attaches and the chunker copies
# onto every chunk: the whole conversation's messages and roles. They stay in
# the canonical chunk file. On an index row they repeat the conversation once
# per turn, which is what grew the old JSON index past its load guard.
DOCUMENT_ONLY_METADATA = frozenset({"messages", "roles"})


def ingested_dir(vault_path) -> Path:
    """The directory canonical chunk files live in."""
    return Path(vault_path) / "memory" / "ingested"


def chunk_file_path(vault_path, chunk_id: str) -> Path:
    """Where a chunk's canonical file lives."""
    return ingested_dir(vault_path) / f"{chunk_id}.json"


def _write_chunk_file(chunk, file_path: Path) -> None:
    chunk_payload = {
        "type": "ingested",
        "source": chunk.source,
        "doc_id": chunk.doc_id,
        "chunk_id": chunk.chunk_id,
        "title": chunk.title,
        "created_at": chunk.created_at,
        "content": chunk.content,
        "metadata": chunk.metadata,
    }
    # Atomic write (ADR-039): an interrupted chunk write can no longer
    # leave a truncated file where a valid chunk already existed.
    with _chunk_file_lock:
        safe_write_json(file_path, chunk_payload)


def _index_metadata(chunk, file_path: Path) -> dict:
    metadata = {
        key: value
        for key, value in (chunk.metadata or {}).items()
        if key not in DOCUMENT_ONLY_METADATA
    }
    metadata.update({
        "source": chunk.source,
        "doc_id": chunk.doc_id,
        "chunk_id": chunk.chunk_id,
        "title": chunk.title,
        "created_at": chunk.created_at,
        # semantic_search reports this as the result's path.
        "file_path": str(file_path),
    })
    return metadata


# Overlapping ingests of one document must write their files and index them as
# one step, or the files can end up on one version and the rows on the other
# (lean review of #315, finding 3). One lock per document in use, counted and
# dropped when its last holder leaves, so the table holds only documents being
# ingested right now. Exact per document: hashing documents onto shared locks
# made unrelated ingests wait on each other. A writer holds one document's
# lock at a time, so these locks cannot deadlock. In-process only: a CLI
# import or backfill must not run alongside the API (KNOWN_ISSUES B-RET-008).
_document_locks: dict[str, list] = {}  # key -> [lock, holders and waiters]
_document_locks_guard = threading.Lock()


@contextmanager
def _document_lock(vault_path, doc_id: str):
    key = f"{Path(vault_path).resolve()}|{doc_id}"
    with _document_locks_guard:
        entry = _document_locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _document_locks_guard:
            entry[1] -= 1
            if entry[1] == 0:
                del _document_locks[key]


def _by_document(chunks) -> list[tuple[str, list]]:
    """Chunks grouped by doc_id, in first-seen order."""
    groups: dict[str, list] = {}
    for chunk in chunks:
        groups.setdefault(chunk.doc_id, []).append(chunk)
    return list(groups.items())


class _DryRunModel:
    """What a store would hold, for a dry run: the rules of
    SqliteVectorStore.insert_if_absent applied to a read-only snapshot, so
    dry-run counts match a real run without opening the store for writing."""

    def __init__(self, db_path: Path, dedupe_type: str | None) -> None:
        self.id_to_key: dict[str, str] = {}
        self.key_count: dict[str, int] = {}
        for row_id, memory_type, key in read_index_snapshot(db_path):
            self.id_to_key[row_id] = key
            if dedupe_type is None or memory_type == dedupe_type:
                self.key_count[key] = self.key_count.get(key, 0) + 1

    def _drop(self, row_id: str) -> None:
        old = self.id_to_key.pop(row_id)
        if old in self.key_count:
            self.key_count[old] -= 1
            if not self.key_count[old]:
                del self.key_count[old]

    def index(self, row_id: str, key: str) -> bool:
        """True when the row would be inserted."""
        old = self.id_to_key.get(row_id)
        if old == key:
            return False
        if old is not None:
            self._drop(row_id)
        if key in self.key_count:
            return False
        self.id_to_key[row_id] = key
        self.key_count[key] = 1
        return True

    def keys(self) -> set[str]:
        return set(self.key_count)


class _IndexRun:
    """One indexing pass over any number of documents, with one summary."""

    def __init__(self, vault_path, *, dry_run: bool, counts: dict[str, int] | None) -> None:
        self.vault_path = vault_path
        self.embeddings_dir = Path(vault_path) / "embeddings"
        self.dry_run = dry_run
        self.counts = counts if counts is not None else {}
        self.counts.update(
            {"ingested.db": 0, "memory.db": 0, "already_indexed": 0, "assistant_gated": 0}
        )
        # db name -> (target, store or dry-run model), opened on first use.
        self._stores: dict[str, tuple] = {}

    def _store(self, target):
        if target.db_name not in self._stores:
            db_path = self.embeddings_dir / target.db_name
            store = (_DryRunModel(db_path, target.dedupe_type) if self.dry_run
                     else get_store(db_path))
            self._stores[target.db_name] = (target, store)
        return self._stores[target.db_name][1]

    def index(self, chunks) -> None:
        for chunk in chunks:
            if not is_indexable(chunk.source, chunk.metadata):
                self.counts["assistant_gated"] += 1
                continue
            target = index_target(chunk.source, chunk.metadata)
            store = self._store(target)
            if self.dry_run:
                inserted = store.index(chunk.chunk_id, content_key(chunk.content))
            else:
                inserted = self._index_one(store, target, chunk)
            self.counts[target.db_name if inserted else "already_indexed"] += 1

    def _index_one(self, store, target, chunk) -> bool:
        """Check against the store's current rows, under its lock. The
        embedding is computed only when the text is absent, outside any lock;
        the second call checks again, so a writer that indexed the same text
        meanwhile wins and this one writes nothing."""
        record = {
            "id": chunk.chunk_id,
            "text": chunk.content,
            "source": chunk.source,
            "memory_type": target.memory_type,
            "created_at": chunk.created_at,
            "authorship": classify_authorship(target.memory_type, chunk.source, chunk.metadata),
            "metadata": _index_metadata(chunk, chunk_file_path(self.vault_path, chunk.chunk_id)),
        }
        outcome = store.insert_if_absent(record, dedupe_type=target.dedupe_type)
        if outcome is IndexOutcome.NEEDS_EMBEDDING:
            record["embedding"] = embed_text(chunk.content)
            outcome = store.insert_if_absent(record, dedupe_type=target.dedupe_type)
        return outcome is IndexOutcome.INSERTED

    def keys(self) -> dict[str, set[str]]:
        """Content keys each touched store holds after the run (modelled on a
        dry run)."""
        return {
            db_name: (store.keys() if self.dry_run else store.content_keys(target.dedupe_type))
            for db_name, (target, store) in self._stores.items()
        }

    def log_summary(self) -> None:
        # Fix-point confirmation (#174): which store each chunk went to.
        # Counts only; chunk ids and text are vault content.
        logger.info(
            "[INGEST] %s %d chunk(s) into ingested.db, %d into memory.db; "
            "skipped %d already indexed, %d held out by ADR-033",
            "would index" if self.dry_run else "indexed",
            self.counts["ingested.db"], self.counts["memory.db"],
            self.counts["already_indexed"], self.counts["assistant_gated"],
        )


def index_chunks(
    chunks, vault_path, *, dry_run: bool = False,
    known_keys: dict[str, set[str]] | None = None,
    counts: dict[str, int] | None = None,
) -> dict[str, int]:
    """Index chunks whose canonical files are already in the vault.

    Returns counts: indexed per store, skipped because the content is
    already indexed, and held out by ADR-033. With dry_run, the same
    decisions are made against a read-only snapshot and counted, and
    nothing is embedded, written or migrated.

    A chunk whose normalized text is already in its target store is skipped
    before embedding, under any id. That makes re-importing a cumulative
    export idempotent and cheap, and keeps the migrated corpus (keyed by file
    path, not chunk_id) from being indexed twice. A chunk whose id is
    indexed with other text has that row replaced, or removed when its new
    text is indexed elsewhere (SqliteVectorStore.insert_if_absent).

    known_keys, when given, is filled per db name with the content keys the
    store holds after the call (on a dry run, would hold). counts, when
    given, is filled as the run goes, so a caller still has the partial
    counts if the run raises.
    """
    run = _IndexRun(vault_path, dry_run=dry_run, counts=counts)
    try:
        for doc_id, group in _by_document(chunks):
            if dry_run:
                run.index(group)
            else:
                with _document_lock(vault_path, doc_id):
                    run.index(group)
    finally:
        run.log_summary()
    if known_keys is not None:
        known_keys.update(run.keys())
    return run.counts


def write_chunks_to_vault(chunks, vault_path) -> dict[str, int]:
    """Write chunks to the vault at vault_path and index the indexable ones.

    Each document's files are written and indexed under that document's
    lock. Returns index_chunks()'s counts.
    """
    ingested_dir(vault_path).mkdir(parents=True, exist_ok=True)
    run = _IndexRun(vault_path, dry_run=False, counts=None)
    try:
        for doc_id, group in _by_document(chunks):
            with _document_lock(vault_path, doc_id):
                for chunk in group:
                    _write_chunk_file(chunk, chunk_file_path(vault_path, chunk.chunk_id))
                run.index(group)
    finally:
        run.log_summary()
    return run.counts
