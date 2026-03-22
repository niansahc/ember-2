from dataclasses import asdict

import ollama
from fastapi import FastAPI
from pydantic import BaseModel

from src.api.chat import router as chat_router
from src.api.openai_adapter import router as openai_adapter_router, llm_adapter
from src.api.routes.ingest import router as ingest_router
from src.context.service import ContextService
from src.memory.service import MemoryService
from src.reflection.generate_reflection import generate_reflection
from src.retrieval.semantic_search import semantic_search
from src.state.models import VALID_STATE_CATEGORIES
from src.state.state_resolver import StateResolver
from src.state.state_service import StateService

app = FastAPI()
app.include_router(chat_router)
app.include_router(openai_adapter_router)
app.include_router(ingest_router)
memory_service = MemoryService()
context_service = ContextService()
state_service = StateService()
state_resolver = StateResolver(service=state_service)


class MemoryRequest(BaseModel):
    text: str
    memory_type: str = "journal"


class StateRequest(BaseModel):
    type: str
    text: str
    source: str = "api"
    tags: list[str] = []
    metadata: dict = {}


class ModelRequest(BaseModel):
    model: str


def clean_context_packet(packet_dict: dict) -> dict:
    for section in ["memory_items", "reflection_items", "state_items"]:
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
def semantic_search_endpoint(
    query: str,
    limit: int = 5,
    memory_type: str | None = None,
    min_score: float | None = None,
):
    return {"results": semantic_search(query, limit, memory_type, min_score)}

@app.post("/reflect")
def reflect_endpoint(memory_type: str = "journal", limit: int = 5):
    return generate_reflection(memory_type=memory_type, limit=limit)


@app.get("/debug-context")
def debug_context_endpoint(message: str):
    context_packet = context_service.build_context(message)
    return clean_context_packet(asdict(context_packet))


@app.get("/state")
def get_state_endpoint():
    items = state_resolver.get_current_state()
    return {"state": [{"category": i.category, "text": i.text, "timestamp": i.timestamp, "priority": i.priority} for i in items]}


@app.get("/state/{category}")
def get_state_by_category_endpoint(category: str):
    if category not in VALID_STATE_CATEGORIES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid category '{category}'. Valid: {sorted(VALID_STATE_CATEGORIES)}")
    item = state_resolver.get_current_by_category(category)
    if not item:
        return {"state": None}
    return {"state": {"category": item.category, "text": item.text, "timestamp": item.timestamp, "priority": item.priority}}


@app.post("/write-state")
def write_state_endpoint(request: StateRequest):
    if request.type not in VALID_STATE_CATEGORIES:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid type '{request.type}'. Valid: {sorted(VALID_STATE_CATEGORIES)}")
    record = StateService.make_record(
        state_type=request.type,
        text=request.text,
        source=request.source,
        tags=request.tags,
        metadata=request.metadata,
    )
    path = state_service.write(record)
    return {"status": "state written", "type": record.type, "text": record.text, "path": str(path)}


@app.get("/model")
def get_model_endpoint():
    try:
        available = [m["model"] for m in ollama.list()["models"]]
    except Exception:
        available = []
    return {"model": llm_adapter.model, "available": available}


@app.post("/model")
def set_model_endpoint(request: ModelRequest):
    llm_adapter.set_model(request.model)
    llm_adapter.prompt_builder.conversation_buffer.set_context_window(request.model)
    return {"model": llm_adapter.model}
