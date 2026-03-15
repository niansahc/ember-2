EMBER_MODEL_ID = "ember-2"

SUPPORTED_MODELS = [EMBER_MODEL_ID]

MEMORY_PREVIEW_LENGTH = 300

import time
import uuid
from typing import List, Optional, Literal

from fastapi import APIRouter
from pydantic import BaseModel

from src.memory.service import MemoryService
from src.context.service import ContextService
from src.llm.adapter import LLMAdapter



router = APIRouter()


memory_service = MemoryService()
context_service = ContextService()
llm_adapter = LLMAdapter()


class OpenAIMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: str


class ChatCompletionsRequest(BaseModel):
    model: str
    messages: List[OpenAIMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False


class ChatCompletionsResponseMessage(BaseModel):
    role: str
    content: str


class ChatCompletionsChoice(BaseModel):
    index: int
    message: ChatCompletionsResponseMessage
    finish_reason: str


class ChatCompletionsResponse(BaseModel):
    id: str
    object: str
    created: int
    model: str
    choices: List[ChatCompletionsChoice]


@router.get("/v1/models")
def list_models():
    return {
        "object": "list",
        "data": [
            {
                "id": model,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "ember",
            }
            for model in SUPPORTED_MODELS
        ],
    }


@router.post("/v1/chat/completions", response_model=ChatCompletionsResponse)
def chat_completions(request: ChatCompletionsRequest):
    user_messages = [m for m in request.messages if m.role == "user"]

    if not user_messages:
        latest_user_message = "Hello."
    else:
        latest_user_message = user_messages[-1].content

    context_packet = context_service.build_context(latest_user_message)
    reply = llm_adapter.generate_response(context_packet)

    max_len = 300
    user_part = latest_user_message[:max_len]
    reply_part = reply[:max_len]

    conversation_memory = f"User asked: {user_part}. Ember responded: {reply_part}"

    memory_service.write(
        text=conversation_memory,
        memory_type="conversation",
        source="chat",
        tags=["conversation"],
    )

    return ChatCompletionsResponse(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        object="chat.completion",
        created=int(time.time()),
        model="ember-2",
        choices=[
            ChatCompletionsChoice(
                index=0,
                message=ChatCompletionsResponseMessage(
                    role="assistant",
                    content=reply,
                ),
                finish_reason="stop",
            )
        ],
    )