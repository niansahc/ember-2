from fastapi import FastAPI
from pydantic import BaseModel

from src.memory.write_memory import write_memory
from src.memory.read_memory import read_journal_memories
from src.memory.search_memory import search_journal_memories

app = FastAPI()


class MemoryRequest(BaseModel):
    text: str


@app.get("/")
def root():
    return {"message": "Ember-2 API is running"}


@app.post("/write-memory")
def write_memory_endpoint(request: MemoryRequest):
    write_memory(request.text)
    return {"status": "memory written"}

@app.get("/read-memories")
def read_memories_endpoint(limit: int = 5):
    return {"memories": read_journal_memories(limit)}

@app.get("/search-memories")
def search_memories_endpoint(query: str, limit: int = 5):
    return {"results": search_journal_memories(query, limit)}