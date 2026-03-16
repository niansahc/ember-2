import json
from pathlib import Path

from src.ingest.importers.gdrive import list_drive_files
from src.ingest.importers.gdrive_download import download_drive_file
from src.ingest.importers.files import load_file
from src.ingest.pipeline import run_ingestion_pipeline
from src.ingest.writers import write_chunks_to_vault


def load_sync_index(sync_index_path):
    if not sync_index_path.exists():
        return {}
    with open(sync_index_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_sync_index(sync_index_path, data):
    sync_index_path.parent.mkdir(parents=True, exist_ok=True)
    with open(sync_index_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def sync_gdrive_folder(folder_id, vault_path):
    query = f"'{folder_id}' in parents and trashed=false"
    files = list_drive_files(query)

    vault_path = Path(vault_path)
    download_dir = vault_path / "imports" / "gdrive"
    sync_index_path = vault_path / "system" / "gdrive_sync_index.json"

    sync_index = load_sync_index(sync_index_path)

    results = []

    for f in files:
        file_id = f["id"]
        file_name = f["name"]
        mime_type = f["mimeType"]
        modified_time = f.get("createdTime") or ""

        prior = sync_index.get(file_id)
        if prior and prior.get("modified_time") == modified_time:
            results.append({"file_name": file_name, "status": "unchanged"})
            continue

        try:
            local_path = download_drive_file(file_id, mime_type, file_name, download_dir)
            docs = load_file(local_path)
            chunks = run_ingestion_pipeline(docs)
            write_chunks_to_vault(chunks, vault_path)

            sync_index[file_id] = {
                "file_name": file_name,
                "mime_type": mime_type,
                "modified_time": modified_time,
                "local_path": str(local_path),
            }

            results.append({
                "file_name": file_name,
                "status": "ingested",
                "chunks_created": len(chunks),
            })

        except ValueError as e:
            results.append({
                "file_name": file_name,
                "status": "skipped",
                "reason": str(e),
            })

    save_sync_index(sync_index_path, sync_index)
    return results