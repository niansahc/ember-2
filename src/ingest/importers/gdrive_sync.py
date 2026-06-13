from pathlib import Path

from src.core.jsonio import safe_read_json, safe_write_json
from src.ingest.importers.gdrive import list_drive_files
from src.ingest.importers.gdrive_download import download_drive_file
from src.ingest.importers.files import load_file
from src.ingest.pipeline import run_ingestion_pipeline
from src.ingest.writers import write_chunks_to_vault


def load_sync_index(sync_index_path):
    # Missing index -> {} silently (first sync); corrupt index -> log + {}
    # (re-sync rather than crash). ADR-039.
    return safe_read_json(sync_index_path, default={})


def save_sync_index(sync_index_path, data):
    # Atomic write so an interrupted sync never leaves a half-written index.
    safe_write_json(sync_index_path, data)


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
        modified_time = f.get("modifiedTime") or ""
        parents = f.get("parents", [])

        prior = sync_index.get(file_id)
        if prior and prior.get("modified_time") == modified_time:
            results.append({"file_name": file_name, "status": "unchanged"})
            continue

        try:
            local_path = download_drive_file(file_id, mime_type, file_name, download_dir)

            docs = load_file(
                local_path,
                source="google_drive",
                doc_id=file_id,
                extra_metadata={
                    "mimeType": mime_type,
                    "modifiedTime": modified_time,
                    "parents": parents,
                    "original_file_name": file_name,
                },
            )

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