import json
import logging
import threading
import time
import uuid
from typing import Any, List, Optional, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("ember.openai_adapter")

from src.api.limiter import limiter
from src.memory.service import MemoryService
from src.memory.write_memory import write_memory
from src.memory.session import create_session, session_exists, get_session
from src.context.service import ContextService
from src.llm.adapter import LLMAdapter
from src.onboarding.service import OnboardingService
from src.state.state_extractor import StateExtractor
from src.state.state_service import StateService


EMBER_MODEL_ID = "ember-2"

SUPPORTED_MODELS = [EMBER_MODEL_ID]


# MEMORY_PREVIEW_LENGTH removed — full conversation text is now stored

model=EMBER_MODEL_ID
# This exists as the acceptable API format for Web

router = APIRouter()


memory_service = MemoryService()
context_service = ContextService()
llm_adapter = LLMAdapter()
onboarding_service = OnboardingService()
state_extractor = StateExtractor()
state_service = StateService()


def _detect_and_write_commitment(reply: str, session_id: str) -> None:
    """Detect commitments in Ember's response and write open_loop state records (ADR-014)."""
    try:
        from src.state.commitment_detector import detect_commitment
        result = detect_commitment(reply)
        if result.detected and result.commitment_text:
            record = state_service.make_record(
                state_type="open_loop",
                text=result.commitment_text,
                source="commitment_detector",
                metadata={"session_id": session_id, "resolved": False},
            )
            state_service.write(record)
            logger.info("[COMMITMENT] Wrote open_loop: %s", result.commitment_text[:60])
    except Exception as exc:
        logger.warning("[COMMITMENT] Detection failed (non-fatal): %s", exc)


def _detect_task_in_response(reply: str, session_id: str) -> None:
    """Detect task-worthy content in Ember's response and store as pending offer."""
    try:
        from src.tasks.task_detector import detect_task
        from src.tasks.task_handler import store_pending_offer
        result = detect_task(reply)
        if result.detected and result.task_title:
            store_pending_offer(session_id, result.task_title)
            logger.info("[TASK_DETECT] Stored pending offer: %s", result.task_title[:60])
    except Exception as exc:
        logger.warning("[TASK_DETECT] Detection failed (non-fatal): %s", exc)


def _background_state_extraction(user_message: str, reply: str) -> None:
    """Run state extraction in a background thread so it doesn't delay the HTTP response."""
    try:
        records = state_extractor.extract(user_message, reply)
        for record in records:
            state_service.write(record)
        if records:
            logger.info("[STATE_EXTRACT] Wrote %d state record(s) to vault", len(records))
    except Exception as exc:
        logger.warning("[STATE_EXTRACT] Background extraction failed (non-fatal): %s", exc)


def _background_deviation_detection(
    response_text: str, intent_class: str,
    user_message: str, prior_response: str | None = None,
) -> None:
    """Run deviation detection in a background thread (ADR-026)."""
    try:
        from src.safety.deviation_detector import detect, write_deviation_record, is_enabled
        if not is_enabled():
            return
        result = detect(
            response_text=response_text,
            intent_class=intent_class,
            prior_response=prior_response,
        )
        if result and result.second_pass_result == "YES":
            write_deviation_record(result, user_message, response_text)
    except Exception as exc:
        logger.warning("[DEVIATION] Background detection failed (non-fatal): %s", exc)


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


def _extract_session_id(request: Request) -> str:
    """
    Read X-Session-ID from request headers.
    If not present, generate a new one.
    """
    session_id = request.headers.get("X-Session-ID", "").strip()
    if not session_id:
        session_id = f"sess_{uuid.uuid4().hex[:16]}"
        logger.info("[SESSION] No X-Session-ID header — generated %s", session_id)
    return session_id


def _ensure_session(session_id: str, first_user_message: str, *, test: bool = False) -> None:
    """
    Create a session record if one doesn't exist for this session_id.
    Title is auto-generated from the first 50 chars of the first user message.
    If test=True, session is flagged as a test session (eval harness).
    """
    if session_exists(session_id):
        return
    title = first_user_message[:50].strip()
    if not title:
        title = "New conversation"
    # Remove trailing partial words if we truncated
    if len(first_user_message) > 50 and " " in title:
        title = title.rsplit(" ", 1)[0] + "..."
    create_session(session_id, title, test=test)
    logger.info("[SESSION] Created session %s: %s%s", session_id, title, " (test)" if test else "")


@router.post("/v1/chat/completions", response_model=ChatCompletionsResponse)
@limiter.limit("30/minute")
async def chat_completions(request: Request, body: ChatCompletionsRequest):
    # --- FILE UPLOAD DIAGNOSTIC LOGGING ---
    try:
        raw_body = await request.body()
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

    # --- SESSION ID ---
    session_id = _extract_session_id(request)

    # (2) Only the last user message is used — Ember's ConversationBuffer
    #     handles conversation history. All prior messages from the request
    #     are intentionally ignored.
    user_messages = [m for m in body.messages if m.role == "user"]

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

    # --- ONBOARDING ---
    if onboarding_service.is_active():
        reply = onboarding_service.handle(latest_user_message)
        return ChatCompletionsResponse(
            id=f"chatcmpl-{uuid.uuid4().hex}",
            object="chat.completion",
            created=int(time.time()),
            model="ember-2",
            choices=[ChatCompletionsChoice(
                index=0,
                message=ChatCompletionsResponseMessage(role="assistant", content=reply),
                finish_reason="stop",
            )],
        )
    # --- END ONBOARDING ---

    # --- TEST SESSION FLAG ---
    is_test = request.headers.get("X-Test-Session", "").strip().lower() == "true"

    # --- ENSURE SESSION EXISTS ---
    _ensure_session(session_id, latest_user_message, test=is_test)

    # --- RESOLVE PROJECT CONTEXT ---
    # Look up the session's project_id so retrieval can boost project-relevant memories.
    project_id = None
    try:
        session_rec = get_session(session_id)
        if session_rec:
            project_id = session_rec.get("metadata", {}).get("project_id")
    except Exception:
        pass  # Non-fatal — proceed without project context

    # --- TASK CREATION (pre-generation) ---
    # Path 1: Explicit task request ("create a task for X")
    # Path 2: Pending offer confirmation ("yes" after Ember offered a task)
    from src.tasks.task_handler import (
        detect_explicit_task_request,
        check_pending_confirmation,
        create_task as create_task_record,
    )

    explicit_task_titles = detect_explicit_task_request(latest_user_message)
    if explicit_task_titles:
        created_titles = []
        failed_titles = []
        for task_title in explicit_task_titles:
            result = create_task_record(
                title=task_title,
                source="user_input",
                session_id=session_id,
                project_id=project_id,
            )
            if result.created:
                created_titles.append(task_title)
            else:
                logger.warning("[TASK] Write failed for '%s': %s", task_title, result.error)
                failed_titles.append(task_title)

        # Inject system context so Ember confirms naturally
        if created_titles:
            titles_str = ", ".join(f'"{t}"' for t in created_titles)
            latest_user_message = f"[System: tasks created - {titles_str}] {latest_user_message}"

    pending_result = check_pending_confirmation(
        session_id=session_id,
        user_message=latest_user_message,
        project_id=project_id,
    )
    if pending_result is not None:
        if pending_result.created and pending_result.task_titles:
            titles_str = ", ".join(f'"{t}"' for t in pending_result.task_titles)
            latest_user_message = f'[System: tasks created - {titles_str}] {latest_user_message}'
        elif not pending_result.created:
            latest_user_message = f'[System: user declined task creation] {latest_user_message}'

    context_packet = context_service.build_context(
        latest_user_message, image_data=image_data, project_id=project_id,
    )

    # Get intent class for grounding check (ADR-019)
    from src.context.policies import classify_query
    _policy = classify_query(latest_user_message)
    _intent_class = _policy.name

    # Stash intent class on request for audit log
    request.state.intent_class = _intent_class

    # Build retrieved context string for grounding check
    _retrieved_context = "\n".join(
        item.content for item in context_packet.memory_items
        if hasattr(item, "content") and item.content
    )

    # Web search transparency: track whether web search results were used
    # in context assembly. Communicated to the UI via X-Ember-Web-Search response
    # header so the client can show a transparency indicator on the message.
    # Uses a header rather than a response body field to avoid breaking
    # OpenAI-compatible response schema.
    used_web_search = bool(context_packet.web_items)

    # Read conversational style preference (casual/balanced/thoughtful)
    from src.core.preferences import get as get_pref
    conversational_style = get_pref("conversational_style", "balanced")

    # Build metadata for memory writes
    user_meta = {"role": "user", "content_kind": "user_content", "session_id": session_id}
    assistant_meta = {"role": "assistant", "content_kind": "answer", "session_id": session_id}
    if project_id:
        user_meta["project_id"] = project_id
        assistant_meta["project_id"] = project_id
    if is_test:
        user_meta["test"] = True
        assistant_meta["test"] = True

    # --- STREAMING PATH ---
    if body.stream:
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"

        from src.safety.grounding_check import (
            should_check_grounding,
            run_grounding_check,
            run_revision_pass,
            log_grounding_outcome,
        )

        _needs_grounding = should_check_grounding(_intent_class)

        def _post_stream_cleanup(full_reply: str) -> None:
            """Shared post-stream cleanup: write memories, extract state, detect tasks."""
            write_memory(
                text=latest_user_message,
                memory_type="conversation",
                source="chat",
                tags=["conversation"],
                metadata=user_meta,
            )
            write_memory(
                text=full_reply,
                memory_type="conversation",
                source="chat",
                tags=["conversation"],
                metadata=assistant_meta,
            )

            # State extraction — skip for test sessions to prevent eval leakage
            if not is_test:
                threading.Thread(
                    target=_background_state_extraction,
                    args=(latest_user_message, full_reply),
                    daemon=True,
                ).start()

            if not is_test:
                threading.Thread(
                    target=_detect_and_write_commitment,
                    args=(full_reply, session_id),
                    daemon=True,
                ).start()

            if not is_test:
                threading.Thread(
                    target=_detect_task_in_response,
                    args=(full_reply, session_id),
                    daemon=True,
                ).start()

            # Deviation detection — async, no latency impact (ADR-026)
            if not is_test:
                _prior = None
                _buffer_turns = prompt_builder.conversation_buffer.get_recent()
                if _buffer_turns and len(_buffer_turns) >= 2:
                    _prior = _buffer_turns[-2].get("assistant")
                threading.Thread(
                    target=_background_deviation_detection,
                    args=(full_reply, _intent_class, latest_user_message, _prior),
                    daemon=True,
                ).start()

            if is_test:
                logger.warning("[TASK] Skipped task/state/commitment detection (test session)")

        def _status_event(status: str) -> str:
            """Format a status SSE event for the UI."""
            return f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': 'ember-2', 'choices': [{'index': 0, 'delta': {'status': status}, 'finish_reason': None}]})}\n\n"

        if _needs_grounding:
            # --- BUFFER-THEN-STREAM PATH (ADR-019) ---
            async def _stream_sse():
                # Searching indicator for web search intent
                if _intent_class == "web_search":
                    yield _status_event("searching")

                # 1. Yield typing indicator
                yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': 'ember-2', 'choices': [{'index': 0, 'delta': {'content': ''}, 'finish_reason': None}]})}\n\n"

                # 2. Generate full response (non-streaming)
                full_reply = llm_adapter.generate_response(context_packet, style=conversational_style)

                # 3. Grounding check
                yield _status_event("verifying")
                is_grounded, unsupported = await run_grounding_check(
                    full_reply, _retrieved_context,
                )

                log_grounding_outcome(
                    intent_class=_intent_class,
                    triggered=True,
                    grounded=is_grounded,
                    revision_triggered=not is_grounded,
                )

                if not is_grounded:
                    yield _status_event("refining")
                    full_reply = await run_revision_pass(
                        full_reply, unsupported or "",
                    )

                # 3.5. Deviation detection (ADR-026) — after grounding, before stream
                if not is_test:
                    _prior = None
                    _buffer_turns = prompt_builder.conversation_buffer.get_recent()
                    if _buffer_turns and len(_buffer_turns) >= 2:
                        _prior = _buffer_turns[-2].get("assistant")
                    threading.Thread(
                        target=_background_deviation_detection,
                        args=(full_reply, _intent_class, latest_user_message, _prior),
                        daemon=True,
                    ).start()

                # 4. Re-stream verified response word by word
                tokens = full_reply.split(" ")
                for i, token in enumerate(tokens):
                    text = token if i == len(tokens) - 1 else token + " "
                    sse_data = json.dumps({
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "ember-2",
                        "choices": [{
                            "index": 0,
                            "delta": {"content": text},
                            "finish_reason": None,
                        }],
                    })
                    yield f"data: {sse_data}\n\n"

                # 5. Web search sources event (if applicable)
                if used_web_search and context_packet.web_items:
                    sources = [
                        {"title": item.get("title", ""), "url": item.get("url", "")}
                        for item in context_packet.web_items
                        if item.get("url")
                    ]
                    if sources:
                        yield f"data: {json.dumps({'type': 'sources', 'sources': sources})}\n\n"

                # Final chunk
                yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': 'ember-2', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                yield "data: [DONE]\n\n"

                _post_stream_cleanup(full_reply)

        else:
            # --- FAST STREAMING PATH (non-grounding intents) ---
            # Casual/social/activity queries: stream tokens as they arrive.
            def _stream_sse():
                accumulated = []

                for chunk in llm_adapter.generate_response_stream(context_packet, style=conversational_style):
                    accumulated.append(chunk)
                    sse_data = json.dumps({
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": int(time.time()),
                        "model": "ember-2",
                        "choices": [{
                            "index": 0,
                            "delta": {"content": chunk},
                            "finish_reason": None,
                        }],
                    })
                    yield f"data: {sse_data}\n\n"

                # Final chunk
                yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': 'ember-2', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                yield "data: [DONE]\n\n"

                full_reply = "".join(accumulated)
                _post_stream_cleanup(full_reply)

        response_headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        if used_web_search:
            response_headers["X-Ember-Web-Search"] = "true"

        return StreamingResponse(
            _stream_sse(),
            media_type="text/event-stream",
            headers=response_headers,
        )

    # --- NON-STREAMING PATH (unchanged) ---
    reply = llm_adapter.generate_response(context_packet, style=conversational_style)

    write_memory(
        text=latest_user_message,
        memory_type="conversation",
        source="chat",
        tags=["conversation"],
        metadata=user_meta,
    )

    write_memory(
        text=reply,
        memory_type="conversation",
        source="chat",
        tags=["conversation"],
        metadata=assistant_meta,
    )

    # Background state extraction — skip for test sessions to prevent eval leakage
    if not is_test:
        threading.Thread(
            target=_background_state_extraction,
            args=(latest_user_message, reply),
            daemon=True,
        ).start()

    # Commitment detection (ADR-014) — skip for test sessions
    if not is_test:
        threading.Thread(
            target=_detect_and_write_commitment,
            args=(reply, session_id),
            daemon=True,
        ).start()

    # Task detection — skip for test sessions
    if not is_test:
        threading.Thread(
            target=_detect_task_in_response,
            args=(reply, session_id),
            daemon=True,
        ).start()

    # Deviation detection — async, no latency impact (ADR-026)
    if not is_test:
        _prior = None
        _buffer_turns = llm_adapter.prompt_builder.conversation_buffer.get_recent()
        if _buffer_turns and len(_buffer_turns) >= 2:
            _prior = _buffer_turns[-2].get("assistant")
        threading.Thread(
            target=_background_deviation_detection,
            args=(reply, _intent_class, latest_user_message, _prior),
            daemon=True,
        ).start()

    if is_test:
        logger.warning("[TASK] Skipped task detection (test session)")

    response_body = ChatCompletionsResponse(
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
    non_stream_headers = {}
    if used_web_search:
        non_stream_headers["X-Ember-Web-Search"] = "true"
    return JSONResponse(content=response_body.model_dump(), headers=non_stream_headers)
