from pathlib import Path
import json

from src.retrieval.embed_memory import embed_text
from src.retrieval.vector_index import VectorIndex


vector_index = VectorIndex()


def write_chunks_to_vault(chunks, vault_path):
    memory_dir = Path(vault_path) / "memory" / "ingested"
    memory_dir.mkdir(parents=True, exist_ok=True)

    index_path = vector_index.get_index_path(Path(vault_path), "ingested")
    index_data = vector_index.load_index(index_path)

    index_map = {item.get("file_path"): item for item in index_data if item.get("file_path")}

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

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(chunk_payload, f, ensure_ascii=False, indent=2)

        embedding = embed_text(chunk.content)

        index_map[str(file_path)] = {
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
        }

    vector_index.save_index(index_path, list(index_map.values()))