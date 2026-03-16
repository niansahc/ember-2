from fastapi import APIRouter

from src.core.config import get_private_vault_path
from src.ingest.importers.chatgpt import load_chatgpt_export
from src.ingest.importers.files import load_file
from src.ingest.pipeline import run_ingestion_pipeline
from src.ingest.writers import write_chunks_to_vault
from src.ingest.importers.gdrive import list_drive_files, parse_gdrive_files
from src.ingest.importers.gdrive_download import download_drive_file

router = APIRouter()


@router.post("/ingest/chatgpt")
def ingest_chatgpt(file_path: str):
    docs = load_chatgpt_export(file_path)
    chunks = run_ingestion_pipeline(docs)

    vault_path = get_private_vault_path()
    write_chunks_to_vault(chunks, vault_path)

    return {"chunks_created": len(chunks)}


@router.post("/ingest/file")
def ingest_file(file_path: str):
    docs = load_file(file_path)
    chunks = run_ingestion_pipeline(docs)

    vault_path = get_private_vault_path()
    write_chunks_to_vault(chunks, vault_path)

    return {"chunks_created": len(chunks)}

@router.post("/ingest/gdrive/list")
def ingest_gdrive_list(query: str):
    files = list_drive_files(query)
    docs = parse_gdrive_files(files)
    return {
        "files_found": len(files),
        "titles": [doc.title for doc in docs],
    }

@router.post("/ingest/gdrive/file")
def ingest_gdrive_file(file_id: str, mime_type: str, file_name: str):
    vault_path = get_private_vault_path()
    download_dir = f"{vault_path}/imports/gdrive"

    local_path = download_drive_file(file_id, mime_type, file_name, download_dir)
    docs = load_file(local_path)
    chunks = run_ingestion_pipeline(docs)
    write_chunks_to_vault(chunks, vault_path)

    return {"chunks_created": len(chunks), "downloaded_to": local_path}