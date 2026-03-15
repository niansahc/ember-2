from fastapi import APIRouter
from pydantic import BaseModel

from src.memory.service import MemoryService
from src.context.service import ContextService
from src.llm.adapter import LLMAdapter

router = APIRouter()

memory_service = MemoryService()
context_service = ContextService()
llm_adapter = LLMAdapter()


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    response: str


@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    context_packet = context_service.build_context(request.message)
    reply = llm_adapter.generate_response(context_packet)

    conversation_memory = f"User asked: {request.message}. Ember responded: {reply}"

    memory_service.write(
        text=conversation_memory,
        memory_type="conversation",
        source="chat",
        tags=["conversation"],
    )

    return ChatResponse(response=reply)