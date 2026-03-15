from dataclasses import asdict

from fastapi import FastAPI
from pydantic import BaseModel

from src.api.chat import router as chat_router
from src.context.service import ContextService
from src.memory.service import MemoryService
from src.reflection.generate_reflection import generate_reflection
from src.retrieval.semantic_search import semantic_search
from src.api.openai_adapter import router as openai_adapter_router
from src.api.routes.ingest import router as ingest_router

app = FastAPI()
app.include_router(chat_router)
app.include_router(openai_adapter_router)
app.include_router(ingest_router)
memory_service = MemoryService()
context_service = ContextService()


class MemoryRequest(BaseModel):
    text: str
    memory_type: str = "journal"


def clean_context_packet(packet_dict: dict) -> dict:
    for section in ["memory_items", "reflection_items"]:
        for item in packet_dict.get(section, []):
            metadata = item.get("metadata", {})

            if "embedding" in metadata:
                del metadata["embedding"]

            if "file_path" in metadata:
                del metadata["file_path"]

    return packet_dict


@app.get("/")
def root():
    return {"message": "Ember-2 API is running"}


@app.post("/write-memory")
def write_memory_endpoint(request: MemoryRequest):
    memory_service.write(request.text, request.memory_type, metadata={})
    return {"status": "memory written"}


@app.get("/read-memories")
def read_memories_endpoint(memory_type: str = "journal", limit: int = 5):
    return {"memories": memory_service.read(memory_type, limit)}


@app.get("/search-memories")
def search_memories_endpoint(query: str, memory_type: str = "journal", limit: int = 5):
    return {"results": memory_service.search(query, memory_type, limit)}


@app.get("/semantic-search")
def semantic_search_endpoint(query: str, limit: int = 5):
    return {"results": semantic_search(query, limit)}


@app.post("/reflect")
def reflect_endpoint(memory_type: str = "journal", limit: int = 5):
    return generate_reflection(memory_type=memory_type, limit=limit)


@app.get("/debug-context")
def debug_context_endpoint(message: str):
    context_packet = context_service.build_context(message)
    return clean_context_packet(asdict(context_packet))
#