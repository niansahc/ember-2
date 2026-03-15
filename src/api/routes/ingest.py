from fastapi import APIRouter
from src.ingest.importers.chatgpt import load_chatgpt_export
from src.ingest.pipeline import run_ingestion_pipeline

router = APIRouter()

@router.post("/ingest/chatgpt")
def ingest_chatgpt(file_path: str):
    docs = load_chatgpt_export(file_path)
    chunks = run_ingestion_pipeline(docs)
    return {"chunks_created": len(chunks)}
