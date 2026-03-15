from fastapi import APIRouter
from pydantic import BaseModel

from src.context.service import ContextService
from src.llm.adapter import LLMAdapter

router = APIRouter()

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
    return ChatResponse(response=reply)