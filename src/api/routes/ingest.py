from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from src.api.limiter import limiter
from src.core.config import get_private_vault_path
from src.ingest.importers.chatgpt import load_chatgpt_export
from src.ingest.importers.files import load_file
from src.ingest.importers.gdrive import (
    list_drive_files,
    parse_gdrive_files,
    get_drive_file,
)
from src.ingest.importers.gdrive_download import download_drive_file
from src.ingest.pipeline import run_ingestion_pipeline
from src.ingest.writers import write_chunks_to_vault
from src.ingest.importers.gdrive_sync import sync_gdrive_folder


router = APIRouter()


def _validate_import_path(file_path: str) -> Path:
    """
    Resolve file_path and confirm it is inside vault/imports/.
    Raises HTTP 400 if the path escapes the allowed directory.
    Raises HTTP 404 if the path does not exist.
    """
    vault_path = get_private_vault_path()
    allowed_root = (vault_path / "imports").resolve()
    resolved = Path(file_path).resolve()

    try:
        resolved.relative_to(allowed_root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"file_path must be inside the vault imports directory: {allowed_root}",
        )

    if not resolved.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {resolved}")

    return resolved


# -----------------------------
# ChatGPT Export Ingestion
# -----------------------------
@router.post("/ingest/chatgpt")
@limiter.limit("10/minute")
def ingest_chatgpt(request: Request, file_path: str):
    safe_path = _validate_import_path(file_path)

    docs = load_chatgpt_export(str(safe_path))
    chunks = run_ingestion_pipeline(docs)

    vault_path = get_private_vault_path()
    write_chunks_to_vault(chunks, vault_path)

    return {
        "documents_loaded": len(docs),
        "chunks_created": len(chunks)
    }


# -----------------------------
# Local File Ingestion
# -----------------------------
@router.post("/ingest/file")
@limiter.limit("10/minute")
def ingest_file(request: Request, file_path: str):
    safe_path = _validate_import_path(file_path)

    docs = load_file(str(safe_path))
    chunks = run_ingestion_pipeline(docs)

    vault_path = get_private_vault_path()
    write_chunks_to_vault(chunks, vault_path)

    return {
        "documents_loaded": len(docs),
        "chunks_created": len(chunks)
    }


# -----------------------------
# Google Drive File Listing
# -----------------------------
@router.post("/ingest/gdrive/list")
def ingest_gdrive_list(query: str = "trashed=false"):

    files = list_drive_files(query)
    docs = parse_gdrive_files(files)

    return {
        "files_found": len(files),
        "files": [
            {
                "id": doc.doc_id,
                "title": doc.title,
                "mime_type": doc.metadata.get("mimeType"),
                "modified_time": doc.metadata.get("modifiedTime"),
                "parents": doc.metadata.get("parents", []),
            }
            for doc in docs
        ]
    }


# -----------------------------
# Google Drive File Ingestion
# -----------------------------
@router.post("/ingest/gdrive/file")
def ingest_gdrive_file(file_id: str):
    vault_path = get_private_vault_path()
    download_dir = f"{vault_path}/imports/gdrive"

    metadata = get_drive_file(file_id)
    file_name = metadata["name"]
    mime_type = metadata["mimeType"]

    try:
        local_path = download_drive_file(file_id, mime_type, file_name, download_dir)

        docs = load_file(
            local_path,
            source="google_drive",
            doc_id=file_id,
            extra_metadata={
                "mimeType": mime_type,
                "modifiedTime": metadata.get("modifiedTime"),
                "parents": metadata.get("parents", []),
                "original_file_name": file_name,
            },
        )

        chunks = run_ingestion_pipeline(docs)
        write_chunks_to_vault(chunks, vault_path)

        return {
            "status": "ingested",
            "file_downloaded": file_name,
            "documents_loaded": len(docs),
            "chunks_created": len(chunks),
        }

    except ValueError as e:
        return {
            "status": "skipped",
            "file_name": file_name,
            "reason": str(e),
        }
# -----------------------------
# Google Drive File Sync
# -----------------------------
@router.post("/ingest/gdrive/folder")
def ingest_gdrive_folder(folder_id: str):
    vault_path = get_private_vault_path()
    results = sync_gdrive_folder(folder_id, vault_path)
    return {"results": results}