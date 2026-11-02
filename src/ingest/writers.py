from pathlib import Path
import logging
import threading

from src.core.jsonio import safe_write_json
from src.retrieval.embed_memory import embed_text
from src.retrieval.vector_index import VectorIndex


logger = logging.getLogger("ember.ingest.writers")

vector_index = VectorIndex()

# Overlapping ingests of one document write the same chunk paths, and on
# Windows os.replace() fails while another thread replaces that file. Held for
# one file write at a time, never across embedding.
_chunk_file_lock = threading.Lock()


def write_chunks_to_vault(chunks, vault_path):
    memory_dir = Path(vault_path) / "memory" / "ingested"
    memory_dir.mkdir(parents=True, exist_ok=True)

    index_path = vector_index.get_index_path(Path(vault_path), "ingested")
    # Embedding takes minutes and other writers use this index meanwhile, so
    # entries are merged at the end rather than saved over an earlier read.
    entries = []

    for chunk in chunks:
        file_path = memory_dir / f"{chunk.chunk_id}.json"

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

        # ADR-033: assistant-role chunks from ChatGPT import write to flat
        # storage for thread reconstruction but are not embedded or indexed.
        if not (chunk.metadata or {}).get("index_for_retrieval", True):
            logger.info(
                "[INGEST] Skipping vector index for chunk "
                "(doc_id=%s chunk_id=%s role=%s)",
                chunk.doc_id,
                chunk.chunk_id,
                (chunk.metadata or {}).get("role"),
            )
            continue

        embedding = embed_text(chunk.content)

        entries.append({
            "file_path": str(file_path),
            "embedding": embedding,
            "text": chunk.content,
            "metadata": {
                **(chunk.metadata or {}),
                "source": chunk.source,
                "doc_id": chunk.doc_id,
                "chunk_id": chunk.chunk_id,
                "title": chunk.title,
                "created_at": chunk.created_at,
            },
        })

    vector_index.merge_entries(index_path, entries)
