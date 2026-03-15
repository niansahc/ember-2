from fastapi import APIRouter

from src.core.config import get_private_vault_path
from src.ingest.importers.chatgpt import load_chatgpt_export
from src.ingest.pipeline import run_ingestion_pipeline
from src.ingest.writers import write_chunks_to_vault
from src.ingest.importers.files import load_text_file
from src.ingest.importers.pdf import load_pdf
from src.ingest.importers.docx import load_docx

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
    docs = load_text_file(file_path)
    chunks = run_ingestion_pipeline(docs)

    vault_path = get_private_vault_path()
    write_chunks_to_vault(chunks, vault_path)

    return {"chunks_created": len(chunks)}

@router.post("/ingest/pdf")
def ingest_pdf(file_path: str):
    docs = load_pdf(file_path)
    chunks = run_ingestion_pipeline(docs)

    vault_path = get_private_vault_path()
    write_chunks_to_vault(chunks, vault_path)

    return {"chunks_created": len(chunks)}

@router.post("/ingest/docx")
def ingest_docx(file_path: str):
    docs = load_docx(file_path)
    chunks = run_ingestion_pipeline(docs)

    vault_path = get_private_vault_path()
    write_chunks_to_vault(chunks, vault_path)

    return {"chunks_created": len(chunks)}