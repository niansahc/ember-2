from fastapi import FastAPI
from pydantic import BaseModel

from src.memory.write_memory import write_memory

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