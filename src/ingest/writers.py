"""
src/ingest/writers.py

Write ingested chunks to the vault and index them where retrieval reads.

Each chunk is written as a canonical JSON file under memory/ingested/ and,
unless ADR-033 keeps it out, embedded into the SQLite store that
src/ingest/index_routing.py names for it: ingested.db for documents,
memory.db as `conversation` for a ChatGPT export (ADR-015). Issue #174:
this used to save every embedding to ingested_index.json, which nothing
reads, so ingested documents were stored but never retrievable.

index_chunks() is the indexing half on its own. The live writer calls it
after writing the files; scripts/backfill_ingested_index.py calls it on
chunk files already in the vault, so both apply one routine.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from src.core.jsonio import safe_write_json
from src.ingest.index_routing import index_target, is_indexable
from src.memory.authorship import classify_authorship
from src.retrieval.embed_memory import embed_text
from src.retrieval.sqlite_vector_store import content_key
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


def index_chunks(
    chunks, vault_path, *, dry_run: bool = False,
    known_keys: dict[str, set[str]] | None = None,
) -> dict[str, int]:
    """Index chunks whose canonical files are already in the vault.

    Returns counts: indexed per store, skipped because the content is
    already indexed, and held out by ADR-033. With dry_run, the same
    decisions are made and counted but nothing is embedded or written, and
    a store that does not exist yet is not created.

    known_keys, when given, is filled per db name with the content keys the
    store holds plus the ones this call indexed (or, on a dry run, would).

    A chunk whose normalized text is already in its target store is skipped
    before embedding, under any id. That makes re-importing a cumulative
    export idempotent and cheap, and keeps the migrated corpus (keyed by file
    path, not chunk_id) from being indexed twice. A chunk whose id is already
    indexed with different text replaces that row (SqliteVectorStore.replace).
    """
    embeddings_dir = Path(vault_path) / "embeddings"
    counts = {"ingested.db": 0, "memory.db": 0, "already_indexed": 0, "assistant_gated": 0}
    if known_keys is None:
        known_keys = {}
    # Store and content keys per db, opened and loaded on first use. One read
    # per call, not per chunk: an export holds thousands of chunks.
    stores: dict[str, tuple] = {}

    for chunk in chunks:
        if not is_indexable(chunk.metadata):
            logger.info(
                "[INGEST] Skipping vector index for chunk "
                "(doc_id=%s chunk_id=%s role=%s)",
                chunk.doc_id,
                chunk.chunk_id,
                (chunk.metadata or {}).get("role"),
            )
            counts["assistant_gated"] += 1
            continue

        target = index_target(chunk.source, chunk.metadata)
        if target.db_name not in stores:
            db_path = embeddings_dir / target.db_name
            opened = None if dry_run and not db_path.exists() else get_store(db_path)
            loaded = known_keys.setdefault(target.db_name, set())
            if opened is not None:
                loaded |= opened.content_keys(target.dedupe_type)
            stores[target.db_name] = (opened, loaded)
        store, keys = stores[target.db_name]

        key = content_key(chunk.content)
        if key in keys:
            counts["already_indexed"] += 1
            continue

        if not dry_run:
            store.replace({
                "id": chunk.chunk_id,
                "text": chunk.content,
                "embedding": embed_text(chunk.content),
                "source": chunk.source,
                "memory_type": target.memory_type,
                "created_at": chunk.created_at,
                "authorship": classify_authorship(target.memory_type, chunk.source, chunk.metadata),
                "metadata": _index_metadata(chunk, chunk_file_path(vault_path, chunk.chunk_id)),
            })
        keys.add(key)
        counts[target.db_name] += 1

    # Fix-point confirmation (#174): which store each chunk went to. Counts
    # only; chunk ids and text are vault content.
    logger.info(
        "[INGEST] %s %d chunk(s) into ingested.db, %d into memory.db; "
        "skipped %d already indexed, %d held out by ADR-033",
        "would index" if dry_run else "indexed",
        counts["ingested.db"], counts["memory.db"],
        counts["already_indexed"], counts["assistant_gated"],
    )
    return counts


def write_chunks_to_vault(chunks, vault_path) -> dict[str, int]:
    """Write chunks to the vault at vault_path and index the indexable ones.

    Returns index_chunks()'s counts.
    """
    chunks = list(chunks)
    ingested_dir(vault_path).mkdir(parents=True, exist_ok=True)
    for chunk in chunks:
        _write_chunk_file(chunk, chunk_file_path(vault_path, chunk.chunk_id))
    return index_chunks(chunks, vault_path)
