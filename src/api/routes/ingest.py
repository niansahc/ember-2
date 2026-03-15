from fastapi import APIRouter
from src.ingest.importers.chatgpt import load_chatgpt_export
from src.ingest.pipeline import run_ingestion_pipeline
from src.ingest.writers import write_chunks_to_vault

router = APIRouter()

@router.post("/ingest/chatgpt")
def ingest_chatgpt(file_path: str):
    docs = load_chatgpt_export(file_path)
    chunks = run_ingestion_pipeline(docs)
    write_chunks_to_vault(chunks, "C:/Users/nians/OneDrive/Desktop/Ember-2/private_vault")
    return {"chunks_created": len(chunks), "written_to": "private_vault/memory/ingested"}