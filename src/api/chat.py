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
    # Build context
    context_packet = context_service.build_context(request.message)

    # Generate response
    reply = llm_adapter.generate_response(context_packet)

    # Store clean conversation memory (no meta wrappers)
    user_part = request.message.strip()
    reply_part = reply.strip()

    # Keep it short but readable
    max_len = 300
    user_part = user_part[:max_len]
    reply_part = reply_part[:max_len]

    memory_service.write(
        text=f"{user_part}\n{reply_part}",
        memory_type="conversation",
        source="chat",
        tags=["conversation"],
        metadata={
            "role": "dialogue",
            "content_kind": "exchange",
        },
    )

    return ChatResponse(response=reply)