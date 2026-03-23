from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.ingest.pipeline import run_ingestion_pipeline
from src.ingest.writers import write_chunks_to_vault
from src.ingest.importers.chatgpt import load_chatgpt_export
from src.core.config import get_private_vault_path


BATCH_SIZE = 50


def main():
    vault = get_private_vault_path()
    folder = vault / "imports" / "chatgpt" / "openai-export"

    docs = load_chatgpt_export(folder)
    print(f"Loaded {len(docs)} conversations")

    total_chunks = 0

    for start in range(0, len(docs), BATCH_SIZE):
        batch = docs[start:start + BATCH_SIZE]
        chunks = run_ingestion_pipeline(batch)
        write_chunks_to_vault(chunks, vault)
        total_chunks += len(chunks)
        print(f"Processed batch {start // BATCH_SIZE + 1}: {len(batch)} conversations, {len(chunks)} chunks")

    print(f"Created {total_chunks} chunks")
    print("Ingestion complete")


if __name__ == "__main__":
    main()