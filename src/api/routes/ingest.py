import base64
import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from pydantic import BaseModel

from src.api.limiter import limiter
from src.core.config import get_private_vault_path
from src.memory.write_memory import write_memory
from src.ingest.importers.chatgpt import load_chatgpt_export
from src.ingest.importers.csv import load_csv
from src.ingest.importers.docx import load_docx
from src.ingest.importers.files import load_file, load_text_file
from src.ingest.importers.gdrive import (
    list_drive_files,
    parse_gdrive_files,
    get_drive_file,
)
from src.ingest.importers.gdrive_download import download_drive_file
from src.ingest.importers.pdf import load_pdf
from src.ingest.pipeline import run_ingestion_pipeline
from src.ingest.writers import write_chunks_to_vault
from src.ingest.importers.gdrive_sync import sync_gdrive_folder

logger = logging.getLogger("ember.ingest")

router = APIRouter()

# File extensions routed to importers
DOCUMENT_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".csv": "csv",
    ".xlsx": "csv",
    ".txt": "txt",
    ".json": "json",
}

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

MIME_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


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
# Multipart File Upload
# -----------------------------
@router.post("/ingest/upload")
@limiter.limit("10/minute")
async def ingest_upload(request: Request, file: UploadFile = File(...)):
    """
    Accept a file upload via multipart form.

    Documents (.pdf, .docx, .csv, .xlsx) are ingested into the vault
    through the standard pipeline: load → clean → chunk → embed → write.

    Images (.jpg, .jpeg, .png, .gif, .webp) are returned as base64
    for use as vision model input — they are NOT ingested into the vault.

    Returns:
      Documents: {"status": "ingested", "filename": "...", "chunks": N}
      Images:    {"status": "image", "data": "base64...", "media_type": "image/..."}
    """
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()

    # --- Image passthrough ---
    if ext in IMAGE_EXTENSIONS:
        content = await file.read()
        b64 = base64.b64encode(content).decode("ascii")
        media_type = MIME_MAP.get(ext, "application/octet-stream")
        logger.info("[UPLOAD] Image passthrough: %s (%s, %d bytes)", filename, media_type, len(content))
        return {
            "status": "image",
            "filename": filename,
            "data": b64,
            "media_type": media_type,
        }

    # --- Document ingestion ---
    if ext not in DOCUMENT_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Supported: {', '.join(sorted(DOCUMENT_EXTENSIONS.keys() | IMAGE_EXTENSIONS))}",
        )

    # Save upload to a temp file, then run through the appropriate importer
    content = await file.read()
    vault_path = get_private_vault_path()
    uploads_dir = vault_path / "imports" / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    # Write to vault/imports/uploads/ so it persists as a source file
    saved_path = uploads_dir / filename
    saved_path.write_bytes(content)
    logger.info("[UPLOAD] Saved %s (%d bytes) to %s", filename, len(content), saved_path)

    try:
        doc_type = DOCUMENT_EXTENSIONS[ext]

        if doc_type == "json":
            # JSON files go through the JSON import path
            json_data = json.loads(saved_path.read_text(encoding="utf-8"))
            result = _import_json_records(json_data)
            return {
                "status": "ingested",
                "filename": filename,
                "imported": result["imported"],
                "errors": result["errors"],
            }
        elif doc_type == "pdf":
            docs = load_pdf(str(saved_path))
        elif doc_type == "docx":
            docs = load_docx(str(saved_path))
        elif doc_type == "csv":
            docs = load_csv(str(saved_path))
        elif doc_type == "txt":
            docs = load_text_file(str(saved_path))
        else:
            raise HTTPException(status_code=400, detail=f"No importer for {ext}")

        chunks = run_ingestion_pipeline(docs)
        write_chunks_to_vault(chunks, vault_path)

        logger.info("[UPLOAD] Ingested %s: %d docs, %d chunks", filename, len(docs), len(chunks))
        return {
            "status": "ingested",
            "filename": filename,
            "documents": len(docs),
            "chunks": len(chunks),
        }

    except Exception as e:
        logger.error("[UPLOAD] Failed to ingest %s: %s", filename, e)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(e)}")


# -----------------------------
# Generic JSON Import
# -----------------------------

def _import_json_records(data: Any) -> dict:
    """
    Import JSON records into the vault using write_memory.

    Accepts a list of records or a single record object.
    Each record must have a "text" field. Optional: type, tags, source, timestamp.

    Returns {"imported": N, "errors": [...]}
    """
    if isinstance(data, dict):
        data = [data]

    if not isinstance(data, list):
        raise HTTPException(
            status_code=422,
            detail="Expected a JSON array of records or a single JSON object.",
        )

    imported = 0
    errors = []

    for i, record in enumerate(data):
        if not isinstance(record, dict):
            errors.append({"index": i, "error": "Record must be a JSON object."})
            continue

        text = record.get("text")
        if not text or not isinstance(text, str) or not text.strip():
            errors.append({"index": i, "error": "Missing or empty 'text' field."})
            continue

        memory_type = record.get("type", "ingested")
        source = record.get("source", "json_import")
        tags = record.get("tags", [])
        if not isinstance(tags, list):
            tags = []

        metadata = {}
        # Pass through optional fields to metadata
        for key in ("content_kind", "timestamp", "source_file"):
            if key in record:
                metadata[key] = record[key]

        result = write_memory(
            text=text.strip(),
            memory_type=memory_type,
            source=source,
            tags=tags,
            metadata=metadata,
        )

        if result:
            imported += 1
        else:
            errors.append({"index": i, "error": "Record skipped by write_memory filters."})

    return {"imported": imported, "errors": errors}


@router.post("/ingest/json")
@limiter.limit("10/minute")
async def ingest_json(request: Request):
    """
    Import structured JSON data into the vault.

    Accepts a JSON array of records or a single JSON object.
    Each record must have at minimum a "text" field.

    Optional fields:
      - type: memory type (defaults to "ingested")
      - tags: list of string tags
      - source: source identifier (defaults to "json_import")
      - content_kind: content classification
      - timestamp: original timestamp

    Returns count of records imported and any validation errors.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid JSON in request body.")

    result = _import_json_records(body)
    return {
        "status": "complete",
        "imported": result["imported"],
        "errors": result["errors"],
    }


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
