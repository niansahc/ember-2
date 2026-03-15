from fastapi import FastAPI
from pydantic import BaseModel

from src.api.chat import router as chat_router
from src.memory.service import MemoryService
from src.retrieval.semantic_search import semantic_search
from src.reflection.generate_reflection import generate_reflection

app = FastAPI()
app.include_router(chat_router)
memory_service = MemoryService()


class MemoryRequest(BaseModel):
    text: str
    memory_type: str = "journal"


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