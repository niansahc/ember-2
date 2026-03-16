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

    incoming_doc_keys = {(chunk.source, chunk.doc_id) for chunk in chunks}

    paths_to_remove = set()

    for chunk_file in memory_dir.glob("*.json"):
        try:
            with open(chunk_file, "r", encoding="utf-8") as f:
                existing_chunk = json.load(f)

            existing_key = (
                existing_chunk.get("source"),
                existing_chunk.get("doc_id"),
            )

            if existing_key in incoming_doc_keys:
                paths_to_remove.add(str(chunk_file))
                chunk_file.unlink()

        except Exception:
            continue

    index_data = [
        item for item in index_data
        if item.get("file_path") not in paths_to_remove
    ]

    existing_paths = {item["file_path"] for item in index_data}

    for chunk in chunks:
        file_path = memory_dir / f"{chunk.chunk_id}.json"

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(chunk.__dict__, f, indent=2)

        if str(file_path) in existing_paths:
            continue

        embedding = embed_text(chunk.content)

        index_data.append({
            "file_path": str(file_path),
            "embedding": embedding,
        })

        existing_paths.add(str(file_path))

    vector_index.save_index(index_path, index_data)