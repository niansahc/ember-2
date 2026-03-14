from fastapi import FastAPI
from pydantic import BaseModel

from src.memory.service import MemoryService

app = FastAPI()
memory_service = MemoryService()


class MemoryRequest(BaseModel):
    text: str
    memory_type: str = "journal"


@app.get("/")
def root():
    return {"message": "Ember-2 API is running"}


@app.post("/write-memory")
def write_memory_endpoint(request: MemoryRequest):
    memory_service.write(request.text, request.memory_type)
    return {"status": "memory written"}

@app.get("/read-memories")
def read_memories_endpoint(memory_type: str = "journal", limit: int = 5):
    return {"memories": memory_service.read(memory_type, limit)}

@app.get("/search-memories")
def search_memories_endpoint(query: str, memory_type: str = "journal", limit: int = 5):
    return {"results": memory_service.search(query, memory_type, limit)}