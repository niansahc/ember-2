import json
import logging
import time
import uuid
from typing import Any, List, Optional, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

logger = logging.getLogger("ember.openai_adapter")

from src.memory.service import MemoryService
from src.context.service import ContextService
from src.llm.adapter import LLMAdapter


EMBER_MODEL_ID = "ember-2"

SUPPORTED_MODELS = [EMBER_MODEL_ID]

MEMORY_PREVIEW_LENGTH = 300

model=EMBER_MODEL_ID
# This exists as the acceptable API format for Web

router = APIRouter()


memory_service = MemoryService()
context_service = ContextService()
llm_adapter = LLMAdapter()


class OpenAIMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: Any  # str normally; list of content parts when files are attached


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
async def chat_completions(raw_request: Request, request: ChatCompletionsRequest):
    # --- FILE UPLOAD DIAGNOSTIC LOGGING ---
    try:
        raw_body = await raw_request.body()
        raw_json = json.loads(raw_body)
        logger.warning("[PAYLOAD] top-level keys: %s", list(raw_json.keys()))
        for i, msg in enumerate(raw_json.get("messages", [])):
            content = msg.get("content")
            if isinstance(content, list):
                logger.warning(
                    "[PAYLOAD] messages[%d] role=%s content=LIST len=%d parts=%s",
                    i, msg.get("role"), len(content),
                    [p.get("type") for p in content if isinstance(p, dict)],
                )
                for j, part in enumerate(content):
                    if isinstance(part, dict):
                        part_keys = list(part.keys())
                        snippet = str(part)[:200]
                        logger.warning("[PAYLOAD]   part[%d] keys=%s snippet=%s", j, part_keys, snippet)
            else:
                snippet = str(content)[:120] if content else ""
                logger.warning(
                    "[PAYLOAD] messages[%d] role=%s content=STR len=%s snippet=%s",
                    i, msg.get("role"), len(content) if content else 0, snippet,
                )
    except Exception as exc:
        logger.warning("[PAYLOAD] failed to log raw request: %s", exc)
    # --- END DIAGNOSTIC LOGGING ---

    # (2) Only the last user message is used — Ember's ConversationBuffer
    #     handles conversation history. All prior messages from the request
    #     are intentionally ignored.
    user_messages = [m for m in request.messages if m.role == "user"]

    if not user_messages:
        latest_user_message = ""
    else:
        raw_content = user_messages[-1].content
        # content is str normally; extract text from list parts when files attached
        if isinstance(raw_content, list):
            text_parts = [p.get("text", "") for p in raw_content if isinstance(p, dict) and p.get("type") == "text"]
            latest_user_message = " ".join(text_parts).strip()
        else:
            latest_user_message = raw_content or ""

    # (1) Empty message guard — Open WebUI sends empty pre-flight requests.
    #     Short-circuit without running the pipeline or writing to memory.
    if not latest_user_message or not latest_user_message.strip():
        logger.warning("[INTERCEPT] Empty user message — returning without pipeline")
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
                        content="I didn't receive a message — please try again.",
                    ),
                    finish_reason="stop",
                )
            ],
        )

    context_packet = context_service.build_context(latest_user_message)
    reply = llm_adapter.generate_response(context_packet)

    max_len = MEMORY_PREVIEW_LENGTH
    user_part = latest_user_message[:max_len]
    reply_part = reply[:max_len]

    memory_service.write(
        text=user_part,
        memory_type="conversation",
        source="chat",
        tags=["conversation"],
        metadata={"role": "user", "content_kind": "user_content"},
    )

    memory_service.write(
        text=reply_part,
        memory_type="conversation",
        source="chat",
        tags=["conversation"],
        metadata={"role": "assistant", "content_kind": "answer"},
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
