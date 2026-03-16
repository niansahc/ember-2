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

    for chunk in chunks:
        file_path = memory_dir / f"{chunk.chunk_id}.json"

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(chunk.__dict__, f, indent=2)

        embedding = embed_text(chunk.content)

        index_data.append({
            "file_path": str(file_path),
            "embedding": embedding
        })

    vector_index.save_index(index_path, index_data)