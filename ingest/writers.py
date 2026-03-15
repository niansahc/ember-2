from pathlib import Path
import json

def write_chunks_to_vault(chunks, vault_path):
    path = Path(vault_path) / "memory" / "ingested"
    path.mkdir(parents=True, exist_ok=True)

    for chunk in chunks:
        file = path / f"{chunk.chunk_id}.json"
        with open(file, "w", encoding="utf-8") as f:
            json.dump(chunk.__dict__, f, indent=2)