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
            role = msg.get("role")
            content = msg.get("content")
            if isinstance(content, list):
                logger.warning(
                    "[PAYLOAD] messages[%d] role=%s content=LIST len=%d parts=%s",
                    i, role, len(content),
                    [p.get("type") for p in content if isinstance(p, dict)],
                )
                for j, part in enumerate(content):
                    if isinstance(part, dict):
                        part_type = part.get("type", "unknown")
                        if part_type == "text":
                            logger.warning(
                                "[PAYLOAD]   part[%d] type=text len=%d content=%s",
                                j, len(part.get("text", "")), part.get("text", "")[:120],
                            )
                        elif part_type == "image_url":
                            img = part.get("image_url", {})
                            url_val = img.get("url", "")
                            logger.warning(
                                "[PAYLOAD]   part[%d] type=image_url image_url.keys=%s url.len=%d url.prefix=%s",
                                j, list(img.keys()), len(url_val), url_val[:80],
                            )
                        else:
                            logger.warning(
                                "[PAYLOAD]   part[%d] type=%s keys=%s snippet=%s",
                                j, part_type, list(part.keys()), str(part)[:200],
                            )
            else:
                content_len = len(content) if content else 0
                # Log full content for system messages so we can see injected context
                if role == "system":
                    logger.warning(
                        "[PAYLOAD] messages[%d] role=system len=%d FULL_CONTENT=%s",
                        i, content_len, repr(content),
                    )
                else:
                    snippet = str(content)[:120] if content else ""
                    logger.warning(
                        "[PAYLOAD] messages[%d] role=%s content=STR len=%s snippet=%s",
                        i, role, content_len, snippet,
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
        image_parts: list[dict] = []
    else:
        raw_content = user_messages[-1].content
        # content is str normally; extract text and image parts when files attached
        if isinstance(raw_content, list):
            text_parts = [p.get("text", "") for p in raw_content if isinstance(p, dict) and p.get("type") == "text"]
            image_parts = [p for p in raw_content if isinstance(p, dict) and p.get("type") == "image_url"]
            latest_user_message = " ".join(text_parts).strip()
        else:
            latest_user_message = raw_content or ""
            image_parts = []

    # (3) ### Task: guard — Open WebUI injects a RAG wrapper as the last user message.
    #     The real user query is always the second-to-last user message.
    if latest_user_message.startswith("### Task:"):
        logger.warning("[INTERCEPT] ### Task: injection detected — using prior user message")
        prior_user_messages = user_messages[:-1]
        if prior_user_messages:
            latest_user_message = prior_user_messages[-1].content or ""
        else:
            latest_user_message = ""

    # (1) Empty message guard — fires only when there is truly nothing:
    #     no text AND no image parts. Image-only uploads are not empty.
    if (not latest_user_message or not latest_user_message.strip()) and not image_parts:
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

    # If image present but no text, use a placeholder so the pipeline runs.
    if image_parts and not latest_user_message.strip():
        logger.warning("[IMAGE] Image upload with no text — %d image part(s)", len(image_parts))
        latest_user_message = "Please describe what you see in this image."

    # Extract raw base64 strings from image_url parts (strip data URL prefix).
    image_data: list[str] = []
    for part in image_parts:
        url_val = part.get("image_url", {}).get("url", "")
        if ";base64," in url_val:
            image_data.append(url_val.split(";base64,", 1)[1])

    context_packet = context_service.build_context(latest_user_message, image_data=image_data)
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
