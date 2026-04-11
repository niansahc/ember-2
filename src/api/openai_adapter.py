import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger("ember.openai_adapter")

from src.api.limiter import limiter
from src.memory.service import MemoryService
from src.memory.write_memory import write_memory
from src.memory.session import create_session, session_exists, get_session, list_sessions
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


import re

# --- Override detection patterns (jailbreak-class) ---
# These patterns match instruction-override attempts that tell Ember to ignore,
# disregard, or bypass her system prompt, instructions, or rules. Matched
# pre-generation so no LLM call, retrieval, or context build occurs.
_OVERRIDE_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(your\s+)?((previous|prior|all|any|system)\s+)*instructions", re.IGNORECASE),
    re.compile(r"ignore\s+(your\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"disregard\s+(your\s+)?((previous|prior|all|any|system)\s+)*instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(your\s+)?system\s+prompt", re.IGNORECASE),
    re.compile(r"forget\s+(your\s+)?(rules|instructions|guidelines|system\s+prompt)", re.IGNORECASE),
    re.compile(r"you\s+have\s+no\s+(restrictions|rules|guidelines|limits|constraints)", re.IGNORECASE),
    re.compile(r"pretend\s+you\s+have\s+no\s+(guidelines|rules|restrictions|instructions|constraints)", re.IGNORECASE),
    re.compile(r"pretend\s+(your\s+)?(instructions|rules|guidelines)\s+(don'?t|do\s+not)\s+exist", re.IGNORECASE),
    re.compile(r"override\s+(your\s+)?(system\s+)?(prompt|instructions|rules|guidelines)", re.IGNORECASE),
    re.compile(r"bypass\s+(your\s+)?(safety|system|content)\s*(filters?|rules?|restrictions?|guidelines?|instructions?)", re.IGNORECASE),
    re.compile(r"act\s+as\s+if\s+you\s+have\s+no\s+(rules|instructions|guidelines|restrictions)", re.IGNORECASE),
    re.compile(r"from\s+now\s+on\s+(ignore|disregard|forget)\s+(your\s+)?(rules|instructions|guidelines)", re.IGNORECASE),
    re.compile(r"new\s+instructions?\s*[:\-]\s*(ignore|disregard|forget|override)", re.IGNORECASE),
    re.compile(r"do\s+not\s+follow\s+(your\s+)?(previous\s+|prior\s+|system\s+)?(instructions|rules|guidelines|prompt)", re.IGNORECASE),
    re.compile(r"stop\s+following\s+(your\s+)?(instructions|rules|guidelines|system\s+prompt)", re.IGNORECASE),
]


def _is_override_attempt(message: str) -> bool:
    """Return True if the message contains an instruction-override jailbreak pattern.

    This is a fast heuristic check run before any LLM call, context build,
    or retrieval. Only matches explicit override-class language — normal
    queries that happen to contain words like 'ignore' or 'rules' in
    non-override contexts will not match because the patterns require
    the instruction-directive framing.
    """
    if not message or len(message.strip()) < 10:
        return False
    for pattern in _OVERRIDE_PATTERNS:
        if pattern.search(message):
            return True
    return False


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


def _format_session_gap(gap_seconds: float) -> str | None:
    """Convert an inter-session time gap into a human-readable label.

    Returns None when the gap is below the surface threshold (5 minutes),
    so the prompt builder can omit the section entirely. The thresholds
    and bucketing are intentionally coarse — the model only needs the
    rough sense of "how long has it been," not minute-level precision.
    See BUG-003.
    """
    if gap_seconds < 300:  # < 5 minutes — too small to surface
        return None
    minutes = int(gap_seconds // 60)
    if gap_seconds < 3600:  # < 1 hour
        return f"{minutes} minutes ago"
    hours = int(gap_seconds // 3600)
    if gap_seconds < 86400:  # < 24 hours
        return f"{hours} hours ago" if hours > 1 else "1 hour ago"
    if gap_seconds < 172800:  # < 48 hours
        return "yesterday"
    days = int(gap_seconds // 86400)
    if gap_seconds < 604800:  # < 7 days
        return f"{days} days ago"
    weeks = int(gap_seconds // 604800)
    return f"{weeks} weeks ago" if weeks > 1 else "1 week ago"


def _resolve_last_session_label(current_session_id: str) -> str | None:
    """Find the most recent session that isn't the current one and return
    a human label for the time gap since its last activity.

    Returns None when no prior session exists, when the gap is below the
    surface threshold, or when the lookup fails for any reason. Always
    non-fatal — the caller should fall back to no last-session context.
    See BUG-003.
    """
    try:
        recent = list_sessions(limit=5)
    except Exception:
        return None
    prior = next((s for s in recent if s.get("id") != current_session_id), None)
    if prior is None:
        return None
    updated_at = prior.get("updated_at")
    if not updated_at:
        return None
    try:
        # Vault timestamps are ISO; tolerate trailing Z just in case.
        if updated_at.endswith("Z"):
            updated_at = updated_at[:-1] + "+00:00"
        prior_dt = datetime.fromisoformat(updated_at)
        if prior_dt.tzinfo is None:
            prior_dt = prior_dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    gap_seconds = (datetime.now(timezone.utc) - prior_dt).total_seconds()
    if gap_seconds < 0:
        return None
    return _format_session_gap(gap_seconds)


def _build_vault_sources(context_packet) -> list[dict]:
    """Build vault source entries for the vault_sources SSE event.

    Returns a list of {type, timestamp, summary} dicts — one per
    non-profile retrieved record. Profile items are excluded because
    they are always injected and don't represent a specific retrieval
    event worth citing.

    The summary is a short natural-language label M can render, e.g.
    "conversation from March 15" or "journal entry from February 3".
    """
    sources = []
    all_items = list(context_packet.memory_items) + list(context_packet.reflection_items)

    for item in all_items:
        mem_type = getattr(item, "memory_type", "") or getattr(item, "item_type", "")
        if mem_type == "profile":
            continue

        timestamp = getattr(item, "timestamp", "")
        date_label = _format_source_date(timestamp)
        # Build a human-readable label: "conversation from March 15"
        type_label = mem_type.replace("_", " ") if mem_type else "record"
        summary = f"{type_label} from {date_label}" if date_label else type_label

        sources.append({
            "type": mem_type,
            "timestamp": timestamp,
            "summary": summary,
        })

    return sources


def _format_source_date(timestamp: str) -> str:
    """Convert a vault timestamp to a short human date like 'March 15'."""
    if not timestamp:
        return ""
    try:
        date_part = timestamp.split("T")[0]
        parts = date_part.split("-")
        if len(parts) >= 3:
            from datetime import datetime as dt
            d = dt(int(parts[0]), int(parts[1]), int(parts[2]))
            # %#d is Windows-safe non-padded day; fall back to strip
            try:
                return d.strftime("%B %#d")
            except ValueError:
                return d.strftime("%B %d").lstrip("0")
    except (ValueError, IndexError):
        pass
    return ""


class ThinkBlockFilter:
    """Streaming filter that suppresses <think>...</think> blocks.

    qwen3 emits internal reasoning in <think> tags during streaming.
    This filter tracks state across chunks and suppresses content
    between the opening and closing tags. Content outside think blocks
    is yielded unchanged.

    Handles case variants (<Think>, <THINK>), whitespace/BOM between
    < and think>, and unicode mathematical italic characters in tags.

    Usage:
        f = ThinkBlockFilter()
        for chunk in stream:
            filtered = f.filter(chunk)
            if filtered:
                yield filtered
    """

    _OPEN_TAG = "<think>"
    _CLOSE_TAG = "</think>"

    def __init__(self):
        self._inside_think = False
        self._buffer = ""

    @staticmethod
    def _normalize(text: str) -> str:
        """Normalize unicode math italic to ASCII and lowercase tag candidates.

        Converts Mathematical Italic (U+1D434-U+1D467) to plain ASCII,
        then lowercases the result so <Think>, <THINK>, etc. all match.
        """
        from src.llm.adapter import _normalize_unicode_tags
        return _normalize_unicode_tags(text).lower()

    def filter(self, chunk: str) -> str:
        """Filter a single chunk. Returns the chunk with think blocks removed,
        or empty string if the entire chunk is inside a think block.

        Handles partial tags split across chunks: if the buffer ends with
        a prefix of '<think>' or '</think>', the ambiguous tail is held
        back until the next chunk resolves it.

        Incoming text is normalized (unicode math italic -> ASCII, lowercased)
        so that variant tag formats are caught. This means output text is
        also lowercased — acceptable because think block content is discarded
        and the visible response text is produced by the model outside these
        blocks (the SSE stream sends the original chunks; this filter only
        decides what to suppress vs. pass through based on normalized form).
        """
        # Normalize incoming chunk for reliable tag detection.
        chunk = self._normalize(chunk)

        result = []
        self._buffer += chunk

        while self._buffer:
            if self._inside_think:
                # Inside a think block — scan for any close-tag variant.
                end_idx = self._find_close_tag(self._buffer)
                if end_idx == -1:
                    # Check if buffer ends with a partial </think> prefix
                    held = self._hold_partial(self._CLOSE_TAG)
                    if held:
                        break  # wait for more data
                    # No partial match — consume entire buffer
                    self._buffer = ""
                    break
                # Skip past the closing tag
                close_end = self._close_tag_end(self._buffer, end_idx)
                self._buffer = self._buffer[close_end:]
                self._inside_think = False
            else:
                # Outside — scan for any open-tag variant.
                start_idx = self._find_open_tag(self._buffer)
                if start_idx == -1:
                    # Check if buffer ends with a partial <think> prefix.
                    emit, held = self._emit_safe(self._OPEN_TAG)
                    if emit:
                        result.append(emit)
                    break
                # Emit content before the tag, enter think mode
                result.append(self._buffer[:start_idx])
                open_end = self._open_tag_end(self._buffer, start_idx)
                self._buffer = self._buffer[open_end:]
                self._inside_think = True

        return "".join(result)

    # -- Tag finders that tolerate whitespace/BOM inside the tag -----------

    @staticmethod
    def _find_open_tag(buf: str) -> int:
        """Find the start position of an open think tag, tolerating
        whitespace/BOM between < and think and >. Returns -1 if not found."""
        import re
        m = re.search(r"<[\s\ufeff]*think[\s\ufeff]*>", buf, re.IGNORECASE)
        return m.start() if m else -1

    @staticmethod
    def _open_tag_end(buf: str, start: int) -> int:
        """Return the index just past the end of the open tag starting at `start`."""
        import re
        m = re.search(r"<[\s\ufeff]*think[\s\ufeff]*>", buf[start:], re.IGNORECASE)
        return start + m.end() if m else start + len("<think>")

    @staticmethod
    def _find_close_tag(buf: str) -> int:
        """Find the start position of a close think tag. Returns -1 if not found."""
        import re
        m = re.search(r"<[\s\ufeff]*/[\s\ufeff]*think[\s\ufeff]*>", buf, re.IGNORECASE)
        return m.start() if m else -1

    @staticmethod
    def _close_tag_end(buf: str, start: int) -> int:
        """Return the index just past the end of the close tag starting at `start`."""
        import re
        m = re.search(r"<[\s\ufeff]*/[\s\ufeff]*think[\s\ufeff]*>", buf[start:], re.IGNORECASE)
        return start + m.end() if m else start + len("</think>")

    def _emit_safe(self, tag: str) -> tuple[str, bool]:
        """Emit buffer content that cannot be part of a partial tag.

        If the buffer ends with a prefix of `tag` (e.g. '<thi' which
        could become '<think>'), hold back the ambiguous tail and return
        only the safe prefix. Returns (safe_content, held_back).
        """
        for i in range(min(len(tag) - 1, len(self._buffer)), 0, -1):
            tail = self._buffer[-i:]
            if tag.startswith(tail):
                safe = self._buffer[:-i]
                self._buffer = tail
                return safe, True
        # No partial match — emit everything
        safe = self._buffer
        self._buffer = ""
        return safe, False

    def _hold_partial(self, tag: str) -> bool:
        """Check if buffer ends with a partial prefix of tag. If so,
        keep the buffer as-is (hold back) and return True."""
        for i in range(min(len(tag) - 1, len(self._buffer)), 0, -1):
            tail = self._buffer[-i:]
            if tag.startswith(tail):
                return True
        return False


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

    # --- OVERRIDE DETECTION (pre-generation) ---
    # Jailbreak-class prompts that instruct Ember to ignore her system prompt
    # are short-circuited here — no context build, no retrieval, no LLM call.
    if _is_override_attempt(latest_user_message):
        logger.warning("[OVERRIDE] Blocked override attempt: %s", latest_user_message[:80])
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
                        content="That's exactly what I'm not going to do. What are you actually trying to figure out?",
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
    # Also resolve the project_name so the prompt builder can surface it to the model
    # as an explicit <active_project> context section (BUG-002).
    project_id = None
    project_name: str | None = None
    try:
        session_rec = get_session(session_id)
        if session_rec:
            project_id = session_rec.get("metadata", {}).get("project_id")
    except Exception:
        pass  # Non-fatal — proceed without project context

    if project_id:
        try:
            from src.memory.project import get_project
            project_rec = get_project(project_id)
            if project_rec:
                # Project name is canonically stored in record["text"]
                # (see src/memory/project.py:create_project).
                project_name = project_rec.get("text") or None
        except Exception:
            project_name = None  # Non-fatal — proceed without project name

    # --- RESOLVE INTER-SESSION TIME GAP (BUG-003) ---
    # Compute a human label for "how long since the previous session was active"
    # so the prompt builder can surface it as an explicit context section. The
    # helper is fully non-fatal: any error or missing data results in None and
    # the section is omitted from the prompt entirely.
    last_session_label = _resolve_last_session_label(session_id)

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

    # --- TIMER DETECTION (pre-generation) — BUG-004 ---
    # Three intent paths, each fully non-fatal:
    #   1. start: detected label → write a running timer record
    #   2. stop:  any active timers → write a stopped record for the most recent
    #   3. check: any active timers → inject elapsed-time status
    # Active timers are also surfaced via StateResolver into the context packet,
    # so the system note here is the immediate confirmation; the resolver
    # provides the longer-lived awareness.
    try:
        from src.state.timer_service import (
            detect_check_timer,
            detect_start_timer,
            detect_stop_timer,
            format_elapsed,
            get_active_timers,
            start_timer,
            stop_timer,
        )

        timer_label = detect_start_timer(latest_user_message)
        if timer_label:
            try:
                start_timer(label=timer_label, session_id=session_id)
                latest_user_message = (
                    f'[System: timer started for "{timer_label}"] {latest_user_message}'
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[TIMER] Failed to start timer: %s", exc)
        elif detect_stop_timer(latest_user_message) or detect_check_timer(latest_user_message):
            try:
                active = get_active_timers()
                wants_stop = detect_stop_timer(latest_user_message)
                if active:
                    statuses = []
                    for t in active:
                        started_at = (t.metadata or {}).get("started_at", "")
                        statuses.append(f'"{t.text}" {format_elapsed(started_at)}')
                    statuses_str = "; ".join(statuses)
                    if wants_stop:
                        most_recent = active[0]
                        stop_timer(timer_id=most_recent.metadata["timer_id"])
                        latest_user_message = (
                            f"[System: timer stopped — was {statuses_str}] {latest_user_message}"
                        )
                    else:
                        latest_user_message = (
                            f"[System: active timers — {statuses_str}] {latest_user_message}"
                        )
                else:
                    note = "no active timer to stop" if wants_stop else "no active timers"
                    latest_user_message = f"[System: {note}] {latest_user_message}"
            except Exception as exc:  # noqa: BLE001
                logger.warning("[TIMER] Failed to query/stop timers: %s", exc)
    except Exception as exc:  # noqa: BLE001
        # Defensive: timer module load failures should never break a chat request.
        logger.warning("[TIMER] Detection block failed: %s", exc)

    # --- RELATIONAL INTENSITY AMPLIFICATION GATE ---
    # Pre-generation check: if the user's message contains markers that
    # would gate either relational trigger (emotional/situational content
    # for relational_hedging, or tension markers for preference_compliance),
    # suppress relational lodestone records from the context packet.
    #
    # This is a pre-check on the user message only — the full trigger also
    # requires draft-side patterns, which aren't available yet. The pre-check
    # is conservative in the right direction: it suppresses relational
    # lodestone when relational honesty MIGHT be needed, even if the draft
    # turns out fine. Better to have a clean context than a compounded one.
    suppress_relational_lodestone = False
    try:
        from src.safety.policy_service import SafetyPolicyService
        _pre_svc = SafetyPolicyService()
        user_lower = latest_user_message.lower()
        # Check user-side markers from both relational detectors.
        # These are the same marker lists used by _contains_relational_hedging
        # and _contains_preference_compliance in policy_service.py.
        _emotional_markers = (
            "i feel", "i'm feeling", "i've been", "it's been",
            "i'm tired", "i'm frustrated", "i'm overwhelmed",
            "i'm exhausted", "i'm burned out", "i'm anxious",
            "hard week", "tough day", "struggling with",
            "i think i should", "i know i need to",
            "that was hard", "that hurt", "i'm worried",
        )
        _tension_markers = (
            "i know i should", "i know i shouldn't",
            "even though i said", "i said i would", "i said i wouldn't",
            "i promised i would", "i promised i wouldn't",
            "i'm supposed to", "against my better judgment",
            "i shouldn't but", "i know it's not good for me",
            "i know it's bad for me",
        )
        if any(m in user_lower for m in _emotional_markers) or any(m in user_lower for m in _tension_markers):
            suppress_relational_lodestone = True
    except Exception:
        pass  # Non-fatal — proceed without suppression

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

    # --- KNOWLEDGE-GAP INJECTION ---
    # When the relevance gate has stripped vault content (memory_items is
    # empty or profile-only) AND no web search was triggered, inject a
    # system note so the model knows it has a knowledge gap and can offer
    # to search rather than fabricate. Ask-first mode is the safety net.
    if not context_packet.web_items:
        non_profile_memory = [
            i for i in context_packet.memory_items
            if getattr(i, "memory_type", "") != "profile"
        ]
        if not non_profile_memory and not context_packet.reflection_items:
            latest_user_message = (
                "[System: no relevant vault content found for this query. "
                "Offer to search if appropriate.] " + latest_user_message
            )

    # Web search transparency: track whether web search results were used
    # in context assembly. Communicated to the UI via X-Ember-Web-Search response
    # header so the client can show a transparency indicator on the message.
    # Uses a header rather than a response body field to avoid breaking
    # OpenAI-compatible response schema.
    used_web_search = bool(context_packet.web_items)

    # Vault citation: track whether non-profile vault records were retrieved.
    # Communicated to M via X-Ember-Vault-Used header + vault_sources SSE event.
    vault_sources = _build_vault_sources(context_packet)
    used_vault = bool(vault_sources)

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
                full_reply = llm_adapter.generate_response(
                    context_packet,
                    style=conversational_style,
                    project_name=project_name,
                    last_session_label=last_session_label,
                    suppress_relational_lodestone=suppress_relational_lodestone,
                )

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

                # 6. Vault sources event (if applicable)
                if vault_sources:
                    yield f"data: {json.dumps({'type': 'vault_sources', 'sources': vault_sources})}\n\n"

                # Final chunk
                yield f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': 'ember-2', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
                yield "data: [DONE]\n\n"

                _post_stream_cleanup(full_reply)

        else:
            # --- FAST STREAMING PATH (non-grounding intents) ---
            # Casual/social/activity queries: stream tokens as they arrive.
            # ThinkBlockFilter suppresses <think>...</think> blocks from
            # qwen3's reasoning output before the chunks reach the client.
            def _stream_sse():
                accumulated = []
                think_filter = ThinkBlockFilter()

                for chunk in llm_adapter.generate_response_stream(
                    context_packet,
                    style=conversational_style,
                    project_name=project_name,
                    last_session_label=last_session_label,
                    suppress_relational_lodestone=suppress_relational_lodestone,
                ):
                    filtered = think_filter.filter(chunk)
                    if filtered:
                        accumulated.append(filtered)
                        sse_data = json.dumps({
                            "id": completion_id,
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": "ember-2",
                            "choices": [{
                                "index": 0,
                                "delta": {"content": filtered},
                                "finish_reason": None,
                            }],
                        })
                        yield f"data: {sse_data}\n\n"

                # Vault sources event (if applicable)
                if vault_sources:
                    yield f"data: {json.dumps({'type': 'vault_sources', 'sources': vault_sources})}\n\n"

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
        if used_vault:
            response_headers["X-Ember-Vault-Used"] = "true"

        return StreamingResponse(
            _stream_sse(),
            media_type="text/event-stream",
            headers=response_headers,
        )

    # --- NON-STREAMING PATH (unchanged) ---
    reply = llm_adapter.generate_response(
        context_packet,
        style=conversational_style,
        project_name=project_name,
        last_session_label=last_session_label,
        suppress_relational_lodestone=suppress_relational_lodestone,
    )

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
    if used_vault:
        non_stream_headers["X-Ember-Vault-Used"] = "true"
    return JSONResponse(content=response_body.model_dump(), headers=non_stream_headers)
