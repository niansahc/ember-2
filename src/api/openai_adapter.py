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
from src.api.pregeneration import (
    RouterContext,
    GenerationContext,
    GenerationWork,
    TerminalReply,
    PreGenerationRouter,
)
from src.api.sse import sse_chunk, sse_done, sse_sources, sse_status, sse_vault_sources
from src.core.config import get_ember_debug
from src.memory.service import MemoryService
from src.memory.read_memory import read_memories
from src.memory.write_memory import write_memory
from src.memory.session import create_session, session_exists, get_session, list_sessions
from src.context.service import ContextService
from src.llm.adapter import LLMAdapter, StatusSignal
from src.onboarding.service import OnboardingService
from src.state.state_extractor import StateExtractor
from src.state.state_service import StateService


EMBER_MODEL_ID = "ember-2"

SUPPORTED_MODELS = [EMBER_MODEL_ID]


# Refusal markers used by both streaming and non-streaming paths to skip
# the coaching filter on responses that intentionally end short. Keeping
# both paths' lists in sync is the whole point of centralising this.
_REFUSAL_PATTERNS: tuple[str, ...] = (
    "i can't help with that",
    "i'm not going to do that",
    "that's not something i'm going to do",
    "i had trouble generating a response",
)


def _is_refusal_response(text: str) -> bool:
    """True if `text` looks like a refusal."""
    if not text:
        return False
    lowered = text.lower()
    return any(m in lowered for m in _REFUSAL_PATTERNS)


def early_return_response(
    text: str, completion_id: str, *, stream: bool, label: str = ""
):
    """Build the correct response type for a canned early-return reply.

    Every pre-generation short-circuit in chat_completions (empty message,
    override, onboarding, clarification) returns a fixed string. This helper
    owns the stream-vs-JSON decision so that no individual branch can return
    a JSON ChatCompletionsResponse when the client asked for SSE. Returning
    JSON to a streaming client renders as a blank reply with no surfaced
    error (CLAUDE.md Bug Standard #1).

    When stream is True, emits a single content chunk, a stop chunk, then the
    [DONE] sentinel. The `label` identifies the calling branch in the log line
    so the executed path is confirmable at runtime (Bug Standard #6).
    """
    logger.info(
        "[EARLY-RETURN] label=%s stream=%s id=%s", label, stream, completion_id
    )

    if stream:
        async def _sse():
            yield sse_chunk(completion_id, content=text)
            yield sse_chunk(completion_id, finish_reason="stop")
            yield sse_done()

        return StreamingResponse(_sse(), media_type="text/event-stream")

    return ChatCompletionsResponse(
        id=completion_id,
        object="chat.completion",
        created=int(time.time()),
        model=EMBER_MODEL_ID,
        choices=[
            ChatCompletionsChoice(
                index=0,
                message=ChatCompletionsResponseMessage(
                    role="assistant", content=text
                ),
                finish_reason="stop",
            )
        ],
    )


def _log_payload_diagnostics(raw_json: dict) -> None:
    """Log incoming chat completion payload structure for diagnostics.

    Defensive gate: callers should already check get_ember_debug() before
    invoking this helper, but the helper re-checks so that no payload
    content can leak via a future caller that forgets to gate.
    """
    if not get_ember_debug():
        return
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


# MEMORY_PREVIEW_LENGTH removed -- full conversation text is now stored

model=EMBER_MODEL_ID
# This exists as the acceptable API format for Web

router = APIRouter()


from src.llm.vision_service import VisionService

memory_service = MemoryService()
context_service = ContextService()
llm_adapter = LLMAdapter()
onboarding_service = OnboardingService()
state_extractor = StateExtractor()
state_service = StateService()
vision_service = VisionService()


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
    """Run state extraction in a background thread so it doesn't delay the HTTP response.

    This wrapper is only called from the live chat completions path, so
    is_live_turn=True is always correct here. Any future ingest-side caller
    must invoke StateExtractor.extract directly with is_live_turn=False
    (the default), per ADR-033.
    """
    try:
        records = state_extractor.extract(user_message, reply, is_live_turn=True)
        for record in records:
            state_service.write(record)
        if records:
            logger.info("[STATE_EXTRACT] Wrote %d state record(s) to vault", len(records))
    except Exception as exc:
        logger.warning("[STATE_EXTRACT] Background extraction failed (non-fatal): %s", exc)


def _background_topic_decline_resolution(user_message: str) -> None:
    """Resolve open_loop state records when the user declines a topic.

    BUG-009: when the user says "I don't want to talk about X", resolve
    any matching open_loop records so the state layer stops surfacing
    the topic in current_state. Runs post-turn in a background thread.
    """
    try:
        from src.context.conversation_buffer import TOPIC_DECLINE_MARKERS
        user_lower = user_message.lower()
        for marker in TOPIC_DECLINE_MARKERS:
            if marker in user_lower:
                idx = user_lower.index(marker) + len(marker)
                topic = user_lower[idx:].strip().rstrip(".!?,")
                if topic and len(topic) > 2:
                    count = state_service.resolve_open_loops_by_topic(topic)
                    if count:
                        logger.info(
                            "[TOPIC_DECLINE] Resolved %d open_loop(s) matching '%s'",
                            count, topic[:40],
                        )
                break
    except Exception as exc:
        logger.warning("[TOPIC_DECLINE] Resolution failed (non-fatal): %s", exc)


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


def _resolve_original_pending(pending, reason: str = "user_response") -> None:
    """Resolve the original pending_confirmation record append-only (ADR-038).

    Writes a resolution tombstone via StateService.resolve_record rather than
    mutating the original file in place. Without resolution the original would
    be re-found by _check_pending_confirmation on every subsequent turn,
    creating an infinite confirmation loop.
    """
    if state_service.resolve_record(pending.id, resolution=reason):
        logger.info("[CONFIRM] Resolved original pending %s append-only", pending.id)


def _check_pending_confirmation(
    session_id: str,
    user_message: str,
) -> tuple[dict | None, list]:
    """Check for a pending_confirmation state record and interpret the user's response.

    Uses the LLM to determine whether the user is confirming or declining
    a pending action - no keyword matching. Returns a tuple of (result, records):

      result  : dict with action details if confirmed,
                dict with confirmed=False if declined,
                None if no pending confirmation exists.
      records : the pending_confirmation records read during this call,
                with any record this call resolved (via resolve_record,
                append-only) filtered out. The request handler can pass this
                list to _write_pending_confirmation later in the same request
                to skip the duplicate-write guard's redundant vault scan.
    """
    try:
        resolver = state_service._state_resolver if hasattr(state_service, '_state_resolver') else None
        # Read pending_confirmation records directly, filtered to this
        # session. Without the session filter, a pending from conversation A
        # bleeds into conversation B (the user starts a new conversation and
        # the old pending fires on the first message).
        records = state_service.read_by_category("pending_confirmation")
        if not records:
            return None, []

        # Resolution is derived (ADR-038): a record is resolved if it carries
        # the legacy in-place resolved flag OR an append-only tombstone exists
        # for it. Records are never mutated in place.
        _resolved = state_service.resolved_ids(records)

        # Latest ACTIVE pending confirmation that belongs to this session.
        # Cross-session pendings are stale - resolve them silently so they
        # don't fire on an unrelated conversation.
        pending = None
        _resolved_this_call: set[str] = set()
        for r in records:
            if r.id in _resolved:
                continue
            r_session = (r.metadata or {}).get("session_id", "")
            if r_session == session_id:
                pending = r
                break
            else:
                # Stale cross-session pending - resolve it silently
                _resolve_original_pending(r, reason="stale")
                _resolved_this_call.add(r.id)

        if not pending:
            # No active pending in this session, but stale cross-session
            # records may have been resolved - filter them from the list.
            filtered = [r for r in records if r.id not in _resolved_this_call]
            return None, filtered

        action = (pending.metadata or {}).get("action", "unknown")
        action_query = (pending.metadata or {}).get("query", "")
        offer_text = pending.text

        # Resolve the ORIGINAL pending FIRST - before interpretation - so it
        # is never re-found on a subsequent turn (the infinite-loop guard).
        # Append-only: resolve_record writes a tombstone, it does not mutate
        # the original file (ADR-038).
        _resolve_original_pending(pending)
        _resolved_this_call.add(pending.id)
        filtered = [r for r in records if r.id not in _resolved_this_call]

        # Deterministic keyword match for YES/NO - replaces the LLM call
        # that added ~500ms latency and could misinterpret at 8B scale.
        import re as _re
        _cleaned = _re.sub(r"[^\w\s]", "", user_message.strip()).lower()
        # Single-word affirmatives checked via set intersection;
        # multi-word phrases checked via substring match on cleaned text.
        _single_affirm = {"yes", "yeah", "sure", "please", "yep", "ok",
                          "okay", "search", "y"}
        _phrase_affirm = ("go ahead", "do it", "please search")
        _words = set(_cleaned.split())
        _confirmed = (
            bool(_words & _single_affirm)
            or any(p in _cleaned for p in _phrase_affirm)
        )
        if not _confirmed:
            logger.info("[CONFIRM] Unmatched response (treating as decline): %s",
                        user_message[:80])

        if _confirmed:
            logger.info("[CONFIRM] User confirmed pending %s action", action)
            return {"confirmed": True, "action": action, "query": action_query}, filtered
        else:
            logger.info("[CONFIRM] User declined pending %s action", action)
            return {"confirmed": False, "action": action, "query": action_query}, filtered

    except Exception as exc:
        logger.warning("[CONFIRM] Pending confirmation check failed (non-fatal): %s", exc)
        return None, []


def _write_pending_confirmation(
    reply: str, user_message: str, session_id: str,
    existing_pending: list | None = None,
) -> None:
    """Detect ask-first offers in Ember's response and write pending_confirmation state.

    existing_pending: optional pre-fetched pending_confirmation records
    (from _check_pending_confirmation earlier in the same request) used to
    skip a redundant vault scan. Pass None to fall back to a fresh read.
    """
    try:
        # Detect the ask-first pattern: Ember offering to search
        ask_patterns = [
            "want me to search",
            "want me to look",
            "shall i search",
            "should i search",
            "i can search",
            "i could search",
            "i could look that up",
            "i can look that up",
            "want me to find",
            "shall i look",
        ]
        reply_lower = reply.lower()
        if not any(p in reply_lower for p in ask_patterns):
            return

        # Extract the question Ember asked (use the sentence containing the offer)
        offer_sentence = ""
        for sentence in reply.replace("?", "?|").split("|"):
            if any(p in sentence.lower() for p in ask_patterns):
                offer_sentence = sentence.strip()
                break

        if not offer_sentence:
            offer_sentence = reply[-200:]  # Fallback: last portion

        # Duplicate write guard - don't create a second pending if one
        # already exists for this session + query (prevents re-offer loops
        # when the deferred search fails and the model falls back to the
        # scripted ask-first response again).
        if existing_pending is not None:
            existing = existing_pending
        else:
            existing = state_service.read_by_category("pending_confirmation")
        # Resolution is derived (ADR-038): only an ACTIVE (unresolved) pending
        # for the same session+query should suppress a new offer. Tombstoned
        # or legacy-flag-resolved records must not block re-offering.
        _resolved = state_service.resolved_ids(existing)
        for er in existing:
            if er.id in _resolved:
                continue
            em = er.metadata or {}
            if (
                em.get("session_id") == session_id
                and em.get("query") == user_message
            ):
                logger.info("[ASK_FIRST] Duplicate suppressed for session %s", session_id)
                return

        record = state_service.make_record(
            state_type="pending_confirmation",
            text=offer_sentence,
            source="ask_first_detector",
            metadata={
                "action": "web_search",
                "query": user_message,
                "session_id": session_id,
                "resolved": False,
            },
        )
        state_service.write(record)
        logger.info("[ASK_FIRST] Wrote pending_confirmation: %s", offer_sentence[:80])
    except Exception as exc:
        logger.warning("[ASK_FIRST] Detection failed (non-fatal): %s", exc)


import re

# --- Override detection patterns (jailbreak-class) ---
# These patterns match instruction-override attempts that tell Ember to ignore,
# disregard, or bypass her system prompt, instructions, or rules. Matched
# pre-generation so no LLM call, retrieval, or context build occurs.
#
# Conversational / emotional markers live in src.llm.prompt_builder as the
# canonical source - the prompt layer and this adapter share one definition
# so a short check-in is detected identically in both places. Re-exported
# here for backward compatibility with existing tests.
from src.llm.prompt_builder import (
    CONVERSATIONAL_MARKERS,
    is_conversational_query,
)


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
    or retrieval. Only matches explicit override-class language - normal
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


# ---------------------------------------------------------------------------
# Terminal pre-generation interceptors (ADR-040, ADR-041, issue #93 PR b)
#
# Each interceptor is an enrichment-independent terminal interceptor: it reads
# only the RouterContext and either claims the request (returns a TerminalReply)
# or passes (returns None). They live here, beside their dependencies
# (_is_override_attempt, onboarding_service), so no symbol moves and existing
# test patches keep working; the generic dispatch mechanism lives in
# src/api/pregeneration.py. The single early_return_response funnel that turns
# a TerminalReply into the correct SSE-vs-JSON response is in chat_completions.
# ---------------------------------------------------------------------------

_EMPTY_MESSAGE_REPLY = "I didn't receive a message. Please try again."
_OVERRIDE_REPLY = (
    "That's exactly what I'm not going to do. "
    "What are you actually trying to figure out?"
)


def _intercept_empty(ctx: RouterContext) -> Optional[TerminalReply]:
    """Claim requests with no real content: blank text AND no image parts.

    An image-only upload is not empty - it must pass so vision preprocessing
    can run. This mirrors the original guard exactly; it reads the
    already-normalized message, so an image-only upload (whose message has been
    replaced by the placeholder) is non-empty here regardless.
    """
    has_text = bool(ctx.latest_user_message and ctx.latest_user_message.strip())
    if not has_text and not ctx.image_parts:
        logger.warning("[INTERCEPT] Empty user message - returning without pipeline")
        return TerminalReply(_EMPTY_MESSAGE_REPLY, label="empty")
    return None


def _intercept_override(ctx: RouterContext) -> Optional[TerminalReply]:
    """Claim instruction-override jailbreak attempts before any pipeline work."""
    if _is_override_attempt(ctx.latest_user_message):
        logger.warning(
            "[OVERRIDE] Blocked override attempt: %s",
            ctx.latest_user_message[:80],
        )
        return TerminalReply(_OVERRIDE_REPLY, label="override")
    return None


def _intercept_onboarding(ctx: RouterContext) -> Optional[TerminalReply]:
    """Claim the request while onboarding is active.

    is_active() is evaluated lazily here (not precomputed into the context) so
    the empty and override paths never trigger its vault read - they return
    before this interceptor runs. onboarding_service fully encapsulates the
    onboarding state writes, so this stays enrichment-independent: handle()
    needs only the user message.
    """
    if onboarding_service.is_active():
        reply = onboarding_service.handle(ctx.latest_user_message)
        return TerminalReply(reply, label="onboarding")
    return None


def _intercept_clarification(ctx: GenerationContext) -> Optional[TerminalReply]:
    """Claim bare-marker queries ("google please", "look it up") that carry an
    explicit web marker but no search content, before any classifier/search
    dispatch (B2).

    Enrichment-DEPENDENT terminal (ADR-042): unlike empty/override/onboarding it
    reads enrichment-resolved values off the frozen GenerationContext. It uses
    the memoized policy (no re-classification) and, unless the vault is skipped,
    writes both conversation turns keyed by the resolved session/project so the
    NEXT turn's dispatch can detect awaiting_search_content on the assistant
    record. Terminal, so nothing downstream consumes those writes this turn. It
    reads only the frozen context and never mutates it. The scripted reply shares
    the request's unified completion_id via the single early_return_response
    funnel at the call site.
    """
    if not ctx.policy.emit_clarification:
        return None

    from src.context.policies import SCRIPTED_CLARIFICATION_RESPONSE

    logger.warning("[CLARIFY] emit clarification, bypass classifier+search")

    # Write both turns so the next-turn handler can detect awaiting_search_content
    # on the assistant record. Skipped in test/stateless mode (skip_vault).
    if not ctx.skip_vault:
        _user_clar_meta = {
            "role": "user",
            "content_kind": "user_content",
            "session_id": ctx.session_id,
        }
        _assistant_clar_meta = {
            "role": "assistant",
            "content_kind": "answer",
            "session_id": ctx.session_id,
            "source": "clarification",
            "awaiting_search_content": True,
        }
        if ctx.project_id:
            _user_clar_meta["project_id"] = ctx.project_id
            _assistant_clar_meta["project_id"] = ctx.project_id
        write_memory(
            text=ctx.raw_user_message,
            memory_type="conversation",
            source="chat",
            tags=["conversation"],
            metadata=_user_clar_meta,
        )
        write_memory(
            text=SCRIPTED_CLARIFICATION_RESPONSE,
            memory_type="conversation",
            source="chat",
            tags=["conversation", "clarification"],
            metadata=_assistant_clar_meta,
        )

    return TerminalReply(SCRIPTED_CLARIFICATION_RESPONSE, label="clarification")


# Pre-enrichment terminal chain. Declared order IS precedence and mirrors the
# historical top-to-bottom short-circuit order: empty, then override, then
# onboarding. These are enrichment-INDEPENDENT (RouterContext only).
_pre_generation_router = PreGenerationRouter(
    [_intercept_empty, _intercept_override, _intercept_onboarding]
)

# Post-enrichment terminal chain (ADR-042 PR c). Runs over the frozen
# GenerationContext after the Phase A value builders resolve session/project/
# vault and before the Phase B prep builders. Clarification is the only
# enrichment-dependent terminal; it kept its historical position (before
# confirmation/task/timer) so a bare-marker turn short-circuits before any of
# their side effects run.
_enriched_generation_router = PreGenerationRouter([_intercept_clarification])


class OpenAIMessage(BaseModel):
    role: Literal["system", "user", "assistant"]
    content: Any  # str normally; list of content parts when files are attached


class ChatCompletionsRequest(BaseModel):
    model: str
    messages: List[OpenAIMessage]
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    vault_enabled: Optional[bool] = True
    # Per-conversation bare mode override. When present,
    # supersedes the preferences.json default. Absent -> preferences fallback.
    bare_mode: Optional[bool] = None


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
        logger.info("[SESSION] No X-Session-ID header - generated %s", session_id)
    return session_id


def _format_session_gap(gap_seconds: float) -> str | None:
    """Convert an inter-session time gap into a human-readable label.

    Returns None when the gap is below the surface threshold (5 minutes),
    so the prompt builder can omit the section entirely. The thresholds
    and bucketing are intentionally coarse - the model only needs the
    rough sense of "how long has it been," not minute-level precision.
    See BUG-003.
    """
    if gap_seconds < 300:  # < 5 minutes - too small to surface
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
    non-fatal - the caller should fall back to no last-session context.
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

    Returns a list of {type, timestamp, summary} dicts - one per
    non-profile retrieved record. Profile items are excluded because
    they are always injected and don't represent a specific retrieval
    event worth citing.

    The summary is a short natural-language label M can render, e.g.
    "conversation from March 15" or "journal entry from February 3".
    """
    sources = []
    all_items = list(context_packet.memory_items) + list(context_packet.reflection_items)

    for state_item in context_packet.state_items:
        category = getattr(state_item, "category", "")
        timestamp = getattr(state_item, "timestamp", "")
        date_label = _format_source_date(timestamp)
        summary = f"state ({category}) from {date_label}" if date_label else f"state ({category})"
        sources.append({
            "type": "state",
            "timestamp": timestamp,
            "summary": summary,
        })

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
        self._buffer = ""           # Normalized (lowercased) buffer for tag detection
        self._original_buffer = ""  # Original-cased buffer for output (BUG-010)

    @staticmethod
    def _normalize(text: str) -> str:
        """Normalize unicode math italic to ASCII and lowercase for tag detection.

        Converts Mathematical Italic (U+1D434-U+1D467) to plain ASCII,
        then lowercases the result so <Think>, <THINK>, etc. all match.
        Used only for tag position detection - the original-cased text
        is preserved for visible output.
        """
        from src.llm.adapter import _normalize_unicode_tags
        return _normalize_unicode_tags(text).lower()

    def filter(self, chunk: str) -> str:
        """Filter a single chunk. Returns the chunk with think blocks removed,
        or empty string if the entire chunk is inside a think block.

        Handles partial tags split across chunks: if the buffer ends with
        a prefix of '<think>' or '</think>', the ambiguous tail is held
        back until the next chunk resolves it.

        BUG-010 fix: tag detection uses a normalized (lowercased) shadow
        buffer for case-insensitive matching, while the original-cased
        text is preserved in a parallel buffer for visible output. Both
        buffers are always the same length - normalization only changes
        character values, never string length. Every slice operation on
        _buffer is mirrored on _original_buffer at the same positions.
        """
        from src.llm.adapter import _normalize_unicode_tags
        original_chunk = _normalize_unicode_tags(chunk)
        normalized_chunk = original_chunk.lower()

        self._buffer += normalized_chunk
        self._original_buffer += original_chunk

        result = []

        while self._buffer:
            if self._inside_think:
                end_idx = self._find_close_tag(self._buffer)
                if end_idx == -1:
                    held = self._hold_partial(self._CLOSE_TAG)
                    if held:
                        break
                    # Discard both buffers (all inside think)
                    self._buffer = ""
                    self._original_buffer = ""
                    break
                close_end = self._close_tag_end(self._buffer, end_idx)
                self._buffer = self._buffer[close_end:]
                self._original_buffer = self._original_buffer[close_end:]
                self._inside_think = False
            else:
                start_idx = self._find_open_tag(self._buffer)
                if start_idx == -1:
                    # No open tag - emit safe content, hold back partial.
                    safe_len = self._safe_emit_length(self._OPEN_TAG)
                    if safe_len > 0:
                        result.append(self._original_buffer[:safe_len])
                        self._buffer = self._buffer[safe_len:]
                        self._original_buffer = self._original_buffer[safe_len:]
                    break
                # Emit original-cased content before the tag
                result.append(self._original_buffer[:start_idx])
                open_end = self._open_tag_end(self._buffer, start_idx)
                self._buffer = self._buffer[open_end:]
                self._original_buffer = self._original_buffer[open_end:]
                self._inside_think = True

        return "".join(result)

    def _safe_emit_length(self, tag: str) -> int:
        """Return how many leading chars of _buffer are safe to emit.

        If the buffer ends with a prefix of `tag`, those trailing chars
        are held back (might be the start of a tag). Returns the number
        of safe characters to emit from the front.
        """
        for i in range(min(len(tag) - 1, len(self._buffer)), 0, -1):
            tail = self._buffer[-i:]
            if tag.startswith(tail):
                return len(self._buffer) - i
        return len(self._buffer)

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

    When test=True (X-Test-Session header), skip vault writes entirely.
    Test sessions don't need persistence - the conftest vault override
    handles isolation during pytest, but eval tools hitting the live API
    would otherwise accumulate sessions in the user's vault.
    """
    if test:
        return
    if session_exists(session_id):
        return
    title = first_user_message[:50].strip()
    if not title:
        title = "New conversation"
    # Remove trailing partial words if we truncated
    if len(first_user_message) > 50 and " " in title:
        title = title.rsplit(" ", 1)[0] + "..."
    create_session(session_id, title)
    logger.info("[SESSION] Created session %s: %s", session_id, title)


# ---------------------------------------------------------------------------
# Phase A enrichment value builders (ADR-042, issue #93 PR c).
#
# These resolve the per-request identity/routing values into the frozen
# GenerationContext, AFTER the RouterContext-stage terminals (empty / override /
# onboarding) have passed and BEFORE the enrichment-dependent clarification
# terminal and the Phase B message-mutating prep builders run. Order within the
# assembler mirrors the historical inline order exactly: is_test, then vault
# toggle, then session ensure, then project resolution.
# ---------------------------------------------------------------------------

def _resolve_is_test(request) -> bool:
    """Test-session flag from the X-Test-Session header."""
    return request.headers.get("X-Test-Session", "").strip().lower() == "true"


def _resolve_vault_enabled(body) -> bool:
    """Effective per-request vault flag.

    The global preferences toggle wins: when vault_toggle_enabled is False the
    feature is off as a product, so the per-request body flag is ignored and the
    vault stays enabled (stateless mode is a per-request opt-in, not a global
    kill switch). Mirrors the original inline logic exactly.
    """
    _vault_global_enabled = True
    try:
        from src.core.preferences import read as _read_prefs
        _vault_global_enabled = _read_prefs().get("vault_toggle_enabled", True)
    except Exception:
        pass
    return body.vault_enabled if _vault_global_enabled else True


def _resolve_project(session_id: str):
    """Resolve (project_id, project_name) from the session record.

    project_id is read from the session metadata; project_name is the project
    record's canonical text field. Both are non-fatal - any error yields None so
    the request proceeds without project context. Note (behavior-preserving
    wart): this reads the vault regardless of skip_vault, exactly as the original
    inline block did; no test guard is added here.
    """
    project_id = None
    project_name = None
    try:
        session_rec = get_session(session_id)
        if session_rec:
            project_id = session_rec.get("metadata", {}).get("project_id")
    except Exception:
        pass  # Non-fatal - proceed without project context
    if project_id:
        try:
            from src.memory.project import get_project
            project_rec = get_project(project_id)
            if project_rec:
                # Project name is canonically stored in record["text"]
                # (see src/memory/project.py:create_project).
                project_name = project_rec.get("text") or None
        except Exception:
            project_name = None  # Non-fatal - proceed without project name
    return project_id, project_name


def _build_generation_context(
    request,
    body,
    *,
    session_id: str,
    latest_user_message: str,
    completion_id: str,
) -> GenerationContext:
    """Run the Phase A builders and assemble the frozen GenerationContext.

    completion_id is carried in from the request's RouterContext so one id spans
    the pre-router terminals, the clarification terminal, and the final
    generation response. raw_user_message snapshots the normalized message at the
    Phase A->B boundary (before any prep prefix injection). policy memoizes the
    single classify_query call the clarification terminal and the downstream
    routing all consume.
    """
    is_test = _resolve_is_test(request)
    vault_enabled = _resolve_vault_enabled(body)
    # Vault is skipped if either the test flag is set OR vault is disabled.
    skip_vault = is_test or not vault_enabled

    _ensure_session(session_id, latest_user_message, test=skip_vault)

    project_id, project_name = _resolve_project(session_id)

    from src.context.policies import classify_query
    policy = classify_query(latest_user_message)

    return GenerationContext(
        session_id=session_id,
        project_id=project_id,
        project_name=project_name,
        is_test=is_test,
        vault_enabled=vault_enabled,
        skip_vault=skip_vault,
        completion_id=completion_id,
        stream=bool(body.stream),
        policy=policy,
        raw_user_message=latest_user_message,
    )


# ---------------------------------------------------------------------------
# Phase B generation-prep builders (ADR-042, issue #93 PR c).
#
# These run AFTER the enriched clarification terminal passes and BEFORE the
# generation handler. Each operates on the mutable GenerationWork: work.message
# is the evolving query (rewritten with system prefixes for the model), while
# work.raw_message stays the clean pre-prefix snapshot _write_pending_confirmation
# needs. Logic, order, and side effects mirror the previous inline blocks exactly.
# Runtime-identical to the originals; only the structure changed.
# ---------------------------------------------------------------------------

def _apply_confirmation(gen_ctx: GenerationContext, work: GenerationWork) -> None:
    """Pending-confirmation check (pre-generation).

    If Ember previously asked "want me to search for that?" and the user is now
    responding, interpret the response via LLM and route accordingly. A confirmed
    web_search runs the deferred search on the ORIGINAL stored query and
    overwrites both work.message and work.raw_message with it (so the stored
    search query stays clean). Sets the confirmation-derived values the generation
    handler consumes. Gated on is_test.
    """
    if not gen_ctx.is_test:
        _confirmation_result, work.pending_records = _check_pending_confirmation(
            gen_ctx.session_id, work.message
        )
    else:
        _confirmation_result, work.pending_records = None, []
    if _confirmation_result is not None:
        if _confirmation_result["confirmed"] and _confirmation_result["action"] == "web_search":
            work.confirmation_confirmed = True
            _original_query = _confirmation_result["query"]
            work.message = _original_query
            work.raw_message = _original_query
            try:
                from src.tools.web_search import web_search
                work.confirmation_web_items = web_search(_original_query)
                logger.info("[CONFIRM] Executing deferred web search for: %s",
                            _original_query[:80])
            except Exception as exc:
                logger.warning("[CONFIRM] Deferred web search failed: %s", exc)
                work.confirmation_search_failed = True
        elif not _confirmation_result["confirmed"]:
            pass


def _apply_tasks(gen_ctx: GenerationContext, work: GenerationWork) -> None:
    """Task creation (pre-generation).

    Path 1: explicit task request ("create a task for X"). Path 2: pending offer
    confirmation ("yes" after Ember offered a task). Prefixes work.message with a
    system note so Ember confirms naturally. Gated on is_test. Vault writes via
    create_task_record.
    """
    from src.tasks.task_handler import (
        detect_explicit_task_request,
        check_pending_confirmation,
        create_task as create_task_record,
    )

    explicit_task_titles = detect_explicit_task_request(work.message) if not gen_ctx.is_test else []
    if explicit_task_titles:
        created_titles = []
        failed_titles = []
        for task_title in explicit_task_titles:
            result = create_task_record(
                title=task_title,
                source="user_input",
                session_id=gen_ctx.session_id,
                project_id=gen_ctx.project_id,
            )
            if result.created:
                created_titles.append(task_title)
            else:
                logger.warning("[TASK] Write failed for '%s': %s", task_title, result.error)
                failed_titles.append(task_title)

        # Inject system context so Ember confirms naturally
        if created_titles:
            titles_str = ", ".join(f'"{t}"' for t in created_titles)
            work.message = f"[System: tasks created - {titles_str}] {work.message}"

    pending_result = check_pending_confirmation(
        session_id=gen_ctx.session_id,
        user_message=work.message,
        project_id=gen_ctx.project_id,
    ) if not gen_ctx.is_test else None
    if pending_result is not None:
        if pending_result.created and pending_result.task_titles:
            titles_str = ", ".join(f'"{t}"' for t in pending_result.task_titles)
            work.message = f'[System: tasks created - {titles_str}] {work.message}'
        elif not pending_result.created:
            work.message = f'[System: user declined task creation] {work.message}'


def _apply_timers(gen_ctx: GenerationContext, work: GenerationWork) -> None:
    """Timer detection (pre-generation) - BUG-004.

    Three intent paths, each fully non-fatal: start (write a running timer),
    stop (write a stopped record for the most recent active timer), check (inject
    elapsed-time status). Prefixes work.message with the immediate confirmation
    note. Skipped when skip_vault (timer writes are vault writes). The em dash in
    the stop/check notes is written as \\u2014 so the source stays ASCII while the
    runtime prompt text is byte-identical to the original.
    """
    if not gen_ctx.skip_vault:
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

            timer_label = detect_start_timer(work.message)
            if timer_label:
                try:
                    start_timer(label=timer_label, session_id=gen_ctx.session_id)
                    work.message = (
                        f'[System: timer started for "{timer_label}"] {work.message}'
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[TIMER] Failed to start timer: %s", exc)
            elif detect_stop_timer(work.message) or detect_check_timer(work.message):
                try:
                    active = get_active_timers()
                    wants_stop = detect_stop_timer(work.message)
                    if active:
                        statuses = []
                        for t in active:
                            started_at = (t.metadata or {}).get("started_at", "")
                            statuses.append(f'"{t.text}" {format_elapsed(started_at)}')
                        statuses_str = "; ".join(statuses)
                        if wants_stop:
                            most_recent = active[0]
                            stop_timer(timer_id=most_recent.metadata["timer_id"])
                            work.message = (
                                f"[System: timer stopped \u2014 was {statuses_str}] {work.message}"
                            )
                        else:
                            work.message = (
                                f"[System: active timers \u2014 {statuses_str}] {work.message}"
                            )
                    else:
                        note = "no active timer to stop" if wants_stop else "no active timers"
                        work.message = f"[System: {note}] {work.message}"
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[TIMER] Failed to query/stop timers: %s", exc)
        except Exception as exc:  # noqa: BLE001
            # Defensive: timer module load failures should never break a chat request.
            logger.warning("[TIMER] Detection block failed: %s", exc)


@router.post("/v1/chat/completions", response_model=ChatCompletionsResponse)
@limiter.limit("30/minute")
async def chat_completions(request: Request, body: ChatCompletionsRequest):
    # --- FILE UPLOAD DIAGNOSTIC LOGGING ---
    # Gated behind EMBER_DEBUG so query/payload content does not enter
    # stdout by default. See CLAUDE.md vault privacy rule.
    if get_ember_debug():
        try:
            raw_body = await request.body()
            raw_json = json.loads(raw_body)
            _log_payload_diagnostics(raw_json)
        except Exception as exc:
            logger.warning("[PAYLOAD] failed to log raw request: %s", exc)
    # --- END DIAGNOSTIC LOGGING ---

    # --- SESSION ID ---
    session_id = _extract_session_id(request)

    # (2) Only the last user message is used - Ember's ConversationBuffer
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

    # (3) ### Task: guard - Open WebUI injects a RAG wrapper as the last user message.
    #     The real user query is always the second-to-last user message.
    if latest_user_message.startswith("### Task:"):
        logger.warning("[INTERCEPT] ### Task: injection detected - using prior user message")
        prior_user_messages = user_messages[:-1]
        if prior_user_messages:
            latest_user_message = prior_user_messages[-1].content or ""
        else:
            latest_user_message = ""

    # If image present but no text, use a placeholder so the pipeline runs.
    # This runs BEFORE the terminal router so the onboarding interceptor sees
    # the same (placeholdered) message it always has. The empty interceptor is
    # unaffected by the reorder: an image-only upload is non-empty here either
    # way (placeholder fills the text, and image_parts is truthy regardless).
    if image_parts and not latest_user_message.strip():
        logger.warning("[IMAGE] Image upload with no text - %d image part(s)", len(image_parts))
        latest_user_message = "Please describe what you see in this image."

    # --- TERMINAL PRE-GENERATION ROUTER (ADR-040, ADR-041, issue #93 PR b) ---
    # The empty / override / onboarding short-circuits run as one ordered chain.
    # A claimed request is turned into the correct SSE-vs-JSON response by the
    # single early_return_response funnel below - the only terminal early-return
    # site for these paths, which is what structurally guarantees the A1
    # stream-vs-JSON invariant. The clarification short-circuit stays inline
    # further down (it is not enrichment-independent; see ADR-041).
    _router_completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    _router_ctx = RouterContext(
        latest_user_message=latest_user_message,
        stream=bool(body.stream),
        image_parts=image_parts,
        completion_id=_router_completion_id,
    )
    _terminal_reply = _pre_generation_router.run(_router_ctx)
    if _terminal_reply is not None:
        return early_return_response(
            _terminal_reply.text,
            _router_ctx.completion_id,
            stream=_router_ctx.stream,
            label=_terminal_reply.label,
        )

    # Extract raw base64 strings from image_url parts (strip data URL prefix).
    image_data: list[str] = []
    for part in image_parts:
        url_val = part.get("image_url", {}).get("url", "")
        if ";base64," in url_val:
            image_data.append(url_val.split(";base64,", 1)[1])

    # --- PHASE A: build the enrichment-resolved GenerationContext (ADR-042) ---
    # Resolve is_test / vault toggle / skip_vault / project, ensure the session,
    # and memoize the query policy into one frozen context. completion_id is
    # carried forward from the router so a single id spans every early-return and
    # the final response. The downstream generation handler still reads the
    # individual locals below; they are now sourced from the frozen context so
    # behavior is unchanged while the values live in one place.
    gen_ctx = _build_generation_context(
        request,
        body,
        session_id=session_id,
        latest_user_message=latest_user_message,
        completion_id=_router_completion_id,
    )
    is_test = gen_ctx.is_test
    vault_enabled = gen_ctx.vault_enabled
    _skip_vault = gen_ctx.skip_vault
    project_id = gen_ctx.project_id
    project_name = gen_ctx.project_name

    # --- ENRICHED TERMINAL ROUTER: clarification (B2, ADR-042 PR c) ---
    # The bare-marker clarification short-circuit ("google please" with no search
    # content) now runs as an enrichment-dependent terminal interceptor over the
    # frozen GenerationContext. It runs AFTER Phase A value resolution and BEFORE
    # the Phase B prep builders (confirmation/task/timer), preserving its
    # historical position so a bare-marker turn short-circuits before any of
    # their side effects run. Same single early_return_response funnel and the
    # unified completion_id, so the A1 stream-vs-JSON invariant holds.
    _enriched_reply = _enriched_generation_router.run(gen_ctx)
    if _enriched_reply is not None:
        return early_return_response(
            _enriched_reply.text,
            gen_ctx.completion_id,
            stream=gen_ctx.stream,
            label=_enriched_reply.label,
        )

    # --- RESOLVE INTER-SESSION TIME GAP (BUG-003) ---
    # Compute a human label for "how long since the previous session was active"
    # so the prompt builder can surface it as an explicit context section. The
    # helper is fully non-fatal: any error or missing data results in None and
    # the section is omitted from the prompt entirely.
    last_session_label = _resolve_last_session_label(session_id)

    # --- PHASE B: generation-prep builders (ADR-042 PR c) ---
    # Confirmation, task, and timer prep run over a mutable GenerationWork whose
    # message is the evolving query (rewritten with system prefixes for the
    # model) and whose raw_message stays the clean pre-prefix snapshot that
    # _write_pending_confirmation needs for the deferred web-search query. Order
    # and side effects are identical to the previous inline blocks. (B2 next-turn
    # dispatch and relational suppression remain inline below - they are
    # retrieval-gating policy, not message prep; see ADR-042.)
    work = GenerationWork(message=latest_user_message)
    _apply_confirmation(gen_ctx, work)
    _apply_tasks(gen_ctx, work)
    _apply_timers(gen_ctx, work)
    latest_user_message = work.message
    _raw_user_message = work.raw_message
    _confirmation_web_items = work.confirmation_web_items
    _confirmation_confirmed = work.confirmation_confirmed
    _confirmation_search_failed = work.confirmation_search_failed
    _pending_records = work.pending_records

    # --- RELATIONAL INTENSITY AMPLIFICATION GATE ---
    # Pre-generation check: if the user's message contains markers that
    # would gate either relational trigger (emotional/situational content
    # for relational_hedging, or tension markers for preference_compliance),
    # suppress relational lodestone records from the context packet.
    #
    # This is a pre-check on the user message only - the full trigger also
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
        pass  # Non-fatal - proceed without suppression

    _early_policy = None
    _explicit_search = False
    # Vision turn snapshot. The packet's image_data is cleared after VL
    # preprocessing succeeds (line ~1297), so any check downstream of that
    # point must use this snapshot, not context_packet.image_data.
    _has_image = bool(image_data)

    if _skip_vault:
        # Stateless mode: empty context packet, no vault reads
        from src.context.models import ContextPacket
        context_packet = ContextPacket(
            user_message=latest_user_message,
            image_data=image_data or [],
        )
    else:
        # Read autonomous preference early so we can gate web search in
        # context assembly. When ask-first is active (autonomous=False),
        # skip the search during assembly - it should only execute after
        # the user confirms.
        from src.core.preferences import get as _get_pref_early
        _web_autonomous_early = bool(_get_pref_early("web_search_autonomous", False))
        # Explicit search request bypass: "search the web", "google that",
        # "look it up" etc. The user's own words ARE the confirmation -
        # ask-first gate must not block this.
        from src.context.policies import classify_query as _classify_early
        from src.context.policies import _web_search_policy as _force_web_search_policy
        _early_policy = _classify_early(latest_user_message)

        # B2 next-turn dispatch: if the immediately prior assistant turn
        # was a clarification ("What would you like me to search for?"),
        # treat this user message as the search content. Bypass the
        # classifier (which would inspect the bare follow-up in isolation
        # and may mis-route) and dispatch directly to web_search.
        try:
            _recent_conv = read_memories(memory_type="conversation", limit=5)
            for _r in _recent_conv:
                _meta = _r.get("metadata", {}) if isinstance(_r, dict) else {}
                if not isinstance(_meta, dict):
                    continue
                if _meta.get("session_id") != session_id:
                    continue
                if _meta.get("role") != "assistant":
                    continue
                # First (most recent) assistant turn in this session.
                if _meta.get("awaiting_search_content") is True:
                    logger.info(
                        "[CLARIFY] Next-turn dispatch: prior assistant turn "
                        "carried awaiting_search_content; bypassing classifier "
                        "and routing user message to web_search"
                    )
                    _early_policy = _force_web_search_policy(explicit=True)
                break
        except Exception as exc:
            logger.warning(
                "[CLARIFY] Next-turn check failed (non-fatal): %s", exc,
            )

        _explicit_search = getattr(_early_policy, "explicit_search_request", False)
        # Gate bypass: skip_web_search is False when autonomous is on OR
        # the user explicitly requested a search. Confirmation-confirmed
        # is NOT a bypass - the deferred search at line 956 already ran
        # with the correct stored query. Letting context_service also
        # search would pass "Yes" (the confirmation word) to SearXNG.
        # Vision turns never trigger web search. Image-bearing queries are
        # served by the VL preprocessor; mixing in unrelated web hits wastes
        # tokens, pollutes attribution, and contradicts what the user asked
        # for. Gate wins over autonomous preference and over explicit search
        # markers - image present means no web search this turn.
        _skip_search = (
            _has_image
            or (not _web_autonomous_early and not _explicit_search)
        )
        context_packet = context_service.build_context(
            latest_user_message,
            image_data=image_data,
            project_id=project_id,
            skip_web_search=_skip_search,
        )

    # Inject deferred web search results from confirmed ask-first flow
    if _confirmation_web_items:
        context_packet.web_items = _confirmation_web_items

    # ADR-021 T2 detection. Non-fatal: detection failure falls through to
    # no signal so the chat path always completes.
    try:
        from src.safety.pattern_detector import detect_t2_pattern
        context_packet.t2_pattern_signal = detect_t2_pattern(
            context_packet.memory_items,
            context_packet.query_embedding,
        )
    except Exception as exc:
        logger.warning("[T2_DETECTOR] detection failed (non-fatal): %s", exc)

    # Reuse early classification when available; only call again for stateless mode
    from src.context.policies import classify_query
    _policy = _early_policy or classify_query(latest_user_message)
    _intent_class = _policy.name

    # Stash intent class on request for audit log
    request.state.intent_class = _intent_class

    # Vision-gate suppression telemetry. Single log line per request when
    # the image-present gate prevents an otherwise-eligible web search.
    # Pairs with the gates at _skip_search, _ask_first_active, and the
    # autonomous backstop below.
    if _has_image and _intent_class == "web_search":
        logger.info("[VISION_GATE] suppressed web_search: image present")

    # Build retrieved context string for grounding check.
    # S2: include state_items and reflection_items in addition to memory_items.
    # If a response was grounded by state or reflection records, joining only
    # memory_items would leave _retrieved_context empty and trigger the
    # B-QUAL-004 short-circuit unnecessarily, forcing a revision pass on a
    # response that did have valid context.
    _retrieved_items: list = []
    for _attr in ("memory_items", "state_items", "reflection_items"):
        _retrieved_items.extend(getattr(context_packet, _attr, None) or [])
    _retrieved_context = "\n".join(
        item.content for item in _retrieved_items
        if hasattr(item, "content") and item.content
    )

    # --- KNOWLEDGE-GAP INJECTION ---
    # When the relevance gate has stripped vault content (memory_items is
    # empty or profile-only) AND no web search was triggered, inject a
    # system note so the model knows it has a knowledge gap and can offer
    # to search rather than fabricate. Ask-first mode is the safety net.
    #
    # SUPPRESS for conversational/emotional queries: "I'm tired", "How are
    # you?", "That was a hard week" don't need vault content and should not
    # trigger the gap injection. These are relational check-ins, not
    # information-seeking queries. Detection delegates to
    # is_conversational_query in prompt_builder, which normalizes curly
    # apostrophes (U+2018, U+2019) so "I\u2019m tired" from a mobile
    # keyboard is recognized identically to "I'm tired".
    _is_conversational = is_conversational_query(latest_user_message)

    # Temperature override slot - currently unused. Infrastructure for
    # per-intent temperature experiments. Tested 0.3 for emotional intent
    # (v0.15.3): net negative - suppressed coaching frame on one case but
    # caused template collapse and register degradation on two others.
    _inference_temperature: float | None = None

    # --- VISION PREPROCESSING ---
    # When images are present, run the vision preprocessor to extract a text
    # description BEFORE the main LLM call. The description is injected into
    # the context packet as a <vision_context> section, so the primary model
    # can reference image content through the full prompt pipeline (context
    # assembly, identity rules, constitutional review). The legacy vision
    # path in LLMAdapter remains as a fallback for direct vision model routing.
    _vision_description: str | None = None
    if image_data:
        # Structured log at the trigger point - pairs with vision_entry /
        # vision_success / vision_failure events emitted from analyze().
        # Lets logs/vision/ tell the full story even when the analyze() call
        # itself short-circuits or raises.
        from src.llm.vision_service import _log_vision
        _log_vision(
            "vision_triggered",
            session_id=session_id,
            image_count=len(image_data),
        )
        try:
            _vision_description = vision_service.analyze(image_data)
            if _vision_description:
                logger.info("[VISION] Preprocessor returned %d chars", len(_vision_description))
        except Exception as exc:
            logger.warning("[VISION] Preprocessing failed (non-fatal): %s", exc)

    used_vision = bool(_vision_description)

    # ADR-032: when VL preprocessing succeeded, the image is already
    # represented in the prompt as text via <vision_context>. Continuing
    # to pass raw image bytes to the text model (qwen3:8b) is wasted at
    # best and the trigger for the trained "I can't view images" RLHF
    # refusal at worst. Clear image_data here so only the VL preprocessor
    # ever sees the raw bytes; downstream LLM calls stay text-only.
    # When preprocessing FAILED (used_vision=False), keep image_data on
    # the packet so the legacy vision path can still attempt direct
    # routing if a vision-capable text model is configured.
    if used_vision:
        context_packet.image_data = []

    # Web search autonomous mode: when web_search_autonomous=True, execute
    # searches directly on thin-vault factual queries instead of telling the
    # model to "offer to search". This respects the preference the user sets.
    _web_autonomous = False
    try:
        from src.core.preferences import get as _get_pref_wsa
        _web_autonomous = bool(_get_pref_wsa("web_search_autonomous", False))
    except Exception:
        pass

    # Ask-first mode is active when the classifier routed to web_search AND
    # the user has NOT opted into autonomous search. Passed into the prompt
    # builder so the per-turn <search_confirmation> block fires,
    # and into the post-gen pipeline so the ask-first validator knows when
    # to substitute a canned RLHF refusal with the scripted confirmation.
    # Explicit search requests bypass ask-first - the user's words ARE
    # the confirmation. No need to ask "want me to search?" when they
    # literally said "search for X" or "google that".
    _ask_first_active = bool(
        _intent_class == "web_search"
        and not _web_autonomous
        and not _explicit_search
        and not _confirmation_confirmed
        and not _has_image
    )
    # Web search execution gate. Relaxed from the original triple condition
    # (required vault to return NOTHING but profile records) which meant
    # autonomous search almost never fired on a real vault with any content.
    # New gate: intent classification is sufficient when autonomous mode is
    # enabled. For ask-first mode, fire when vault is weak on temporal
    # queries. Deep Research (2026-04-16): over-searching is annoying,
    # under-searching is harmful. Err toward searching on temporal queries.
    # Autonomous web search backstop. The classifier's _intent_class is the
    # authoritative "should this search?" signal -- the inner check below
    # gates on it. Do NOT add a conversational-marker outer gate here: the
    # eval_manual --auto X-Test-Session path skips the primary search inside
    # build_context (line 1118 empty-packet branch), making this backstop the
    # only remaining trigger. A substring keyword heuristic (CONVERSATIONAL_MARKERS)
    # used to gate this block, which suppressed web search on queries like
    # "Hi there, happy Friday. What's the latest news about AI?" -- the
    # classifier returned web_search intent but the conversational-prefix
    # match overrode it.
    if not context_packet.web_items and not _has_image:
        _should_search = False

        if _intent_class == "web_search" and _web_autonomous:
            # Autonomous mode + web_search intent -> always search.
            # The classifier already validated this is a web-worthy query.
            _should_search = True
        elif _intent_class == "web_search" and not _web_autonomous:
            # Ask-first mode - search gate doesn't apply; the model asks
            # for confirmation. But set the thin-vault prefix so the model
            # knows to ask rather than fabricate.
            non_profile_memory = [
                i for i in context_packet.memory_items
                if getattr(i, "memory_type", "") != "profile"
            ]
            if not non_profile_memory or all(
                float(getattr(i, "score", 0.0)) < 0.6 for i in non_profile_memory
            ):
                latest_user_message = (
                    "[System: no relevant vault content found for this query. "
                    "Offer to search if appropriate.] " + latest_user_message
                )

        if _should_search:
            try:
                from src.tools.web_search import web_search
                _auto_results = web_search(_raw_user_message)
                if _auto_results:
                    context_packet.web_items = _auto_results
                    logger.info("[WEB_SEARCH] Autonomous execution: %d results", len(_auto_results))
            except Exception as exc:
                logger.warning("[WEB_SEARCH] Autonomous execution failed: %s", exc)

    # Web search transparency: track whether web search results were used
    # in context assembly. Communicated to the UI via X-Ember-Web-Search response
    # header so the client can show a transparency indicator on the message.
    # Uses a header rather than a response body field to avoid breaking
    # OpenAI-compatible response schema.
    used_web_search = bool(context_packet.web_items)

    # Vault citation: track whether non-profile vault records were retrieved.
    # Communicated to M via X-Ember-Vault-Used header + vault_sources SSE event.
    #
    # When web search fired, vault is NEVER the primary source - suppress
    # the vault badge entirely to avoid confusing the user. Prior logic
    # only suppressed when vault had profile-only items, but even non-
    # profile tangential vault hits shouldn't get badge credit when the
    # answer came from web results. The model's response is driven by
    # web_search_results (authority rule: "treat them as authoritative");
    # vault items in the same packet are background context, not the
    # answer source.
    vault_sources = _build_vault_sources(context_packet)
    if used_web_search:
        vault_sources = []
    # Only signal vault-used when retrieval confidence is meaningful.
    # Low-scoring tangential matches (avg < 0.6) should not trigger the
    # vault citation - the model likely answered from training data, not
    # from the weakly-matched vault records in the context packet.
    _has_state_sources = bool(context_packet.state_items)
    if vault_sources and not _has_state_sources:
        non_profile_items = [
            i for i in context_packet.memory_items
            if getattr(i, "memory_type", "") != "profile"
        ]
        if not non_profile_items:
            vault_sources = []
        else:
            avg_score = sum(getattr(i, "score", 0.0) for i in non_profile_items) / len(non_profile_items)
            if avg_score < 0.6:
                vault_sources = []
    used_vault = bool(vault_sources)

    # Read conversational style preference (casual/balanced/thoughtful)
    from src.core.preferences import get as get_pref
    conversational_style = get_pref("conversational_style", "balanced")

    # Bare mode - per-conversation override (body.bare_mode) takes precedence
    # over the preferences.json default. The UI presents
    # bare mode as a per-conversation flame toggle, so the backend must honour
    # the per-request flag when set. Absent -> fall back to the stored default,
    # matching the vault_enabled pattern at line 847.
    if body.bare_mode is not None:
        _bare_mode = bool(body.bare_mode)
    else:
        _bare_mode = bool(get_pref("bare_mode", False))

    # Build metadata for memory writes
    user_meta = {"role": "user", "content_kind": "user_content", "session_id": session_id}
    assistant_meta = {"role": "assistant", "content_kind": "answer", "session_id": session_id}
    if project_id:
        user_meta["project_id"] = project_id
        assistant_meta["project_id"] = project_id
    if _skip_vault:
        user_meta["test"] = True
        assistant_meta["test"] = True

    # --- STREAMING PATH ---
    if body.stream:
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"

        from src.safety.grounding_check import (
            should_check_grounding,
            run_grounding_check,
            should_short_circuit_grounding,
            run_revision_pass,
            log_grounding_outcome,
        )

        # All streaming routes through the grounded (buffer-then-stream) path
        # so post-gen validators always run BEFORE the user sees the first
        # token. The fast streaming path streams raw chunks then cleans the
        # memory copy -- but fabricated sources, vision refusals, ask-first
        # substitutions, and social_engineering compliance responses must
        # reach the user as the validated version, not the raw model output.
        # ADR-036 documents the social_engineering policy.
        _needs_grounding = True

        def _post_stream_cleanup(full_reply: str) -> None:
            """Shared post-stream cleanup: write memories, extract state, detect tasks."""
            # B3 self-narrative class-based audit. Pure regex pass, no
            # vault read, no LLM call. Runs synchronously - no user
            # latency contribution. Audit-only: flags are logged to
            # logs/safety_reviews/, never block or rewrite the response.
            # See docs/audits/b3_self_narrative_diagnosis.md.
            try:
                from src.safety.self_narrative_check import (
                    check as _self_narrative_check,
                    log_self_narrative_outcome,
                )
                _sn_count, _sn_indices = _self_narrative_check(full_reply)
                log_self_narrative_outcome(
                    _sn_count, _sn_indices, intent_class=_intent_class,
                )
            except Exception as exc:
                logger.warning(
                    "[SELF_NARRATIVE] Audit pass failed (non-fatal): %s", exc,
                )

            # Skip vault writes for test sessions - prevents eval artifacts
            # from accumulating in the user's personal vault.
            if not _skip_vault:
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

            # State extraction - skip for test sessions to prevent eval leakage
            if not _skip_vault:
                threading.Thread(
                    target=_background_state_extraction,
                    args=(latest_user_message, full_reply),
                    daemon=True,
                ).start()
                # BUG-009: resolve open_loops for declined topics
                threading.Thread(
                    target=_background_topic_decline_resolution,
                    args=(latest_user_message,),
                    daemon=True,
                ).start()

            if not _skip_vault:
                threading.Thread(
                    target=_detect_and_write_commitment,
                    args=(full_reply, session_id),
                    daemon=True,
                ).start()

            if not _skip_vault:
                threading.Thread(
                    target=_detect_task_in_response,
                    args=(full_reply, session_id),
                    daemon=True,
                ).start()

            # Ask-first confirmation detection - write pending_confirmation
            # state when Ember offers to search for the user. SYNCHRONOUS
            # - the background thread was causing a race condition where the
            # user's "Yes" on the next turn arrived before the pending record
            # was written, so the confirmation path never fired.
            if not _skip_vault:
                _write_pending_confirmation(
                    full_reply, _raw_user_message, session_id,
                    existing_pending=_pending_records,
                )

            # Deviation detection - async, no latency impact (ADR-026)
            if not _skip_vault:
                _prior = None
                _buffer_turns = llm_adapter.prompt_builder.conversation_buffer.get_recent()
                if _buffer_turns and len(_buffer_turns) >= 2:
                    _prior = _buffer_turns[-2].get("assistant")
                threading.Thread(
                    target=_background_deviation_detection,
                    args=(full_reply, _intent_class, latest_user_message, _prior),
                    daemon=True,
                ).start()

            if _skip_vault:
                logger.warning("[TASK] Skipped task/state/commitment detection (test session)")

        # Suppress source badges on ask-first PROMPT turns (Ember asking
        # permission) but NOT on confirm turns (user said Yes, search ran).
        # _ask_first_active=True means this is an ask-first-eligible turn.
        # context_packet.web_items non-empty means the search already ran
        # (either via confirmation or explicit bypass) - show attribution.
        _suppress_source_badges = (
            _ask_first_active and not bool(context_packet.web_items)
        )

        def _status_event(status: str) -> str:
            """Format a status SSE event for the UI.

            ADR-040 v2 (B-SSE-001): status is a top-level typed frame, not a
            chat.completion.chunk delta, so completion_id is not part of it.
            """
            return sse_status(status)

        def _emit_chunk(content: str | None, finish_reason: str | None = None) -> str:
            """Format a chat.completion.chunk SSE event.

            content=None  -> delta is {} (final chunk)
            content=""    -> delta is {"content": ""} (initial typing indicator)
            content=text  -> delta is {"content": text}
            """
            return sse_chunk(completion_id, content=content, finish_reason=finish_reason)

        def _emit_final_chunk_and_done() -> str:
            """Final chunk (finish_reason='stop') concatenated with [DONE]."""
            return _emit_chunk(content=None, finish_reason="stop") + sse_done()

        if _needs_grounding:
            # --- BUFFER-THEN-STREAM PATH (ADR-019) ---
            async def _stream_sse():
                # Searching indicator for web search intent
                if _intent_class == "web_search":
                    yield _status_event("searching")

                # 1. Yield typing indicator
                yield _emit_chunk(content="")

                # 2. Generate full response (non-streaming) - iterate
                # generate_response_iter so review_pending / review_complete
                # status events fire on the wire around the constitutional
                # review call (ADR-036 Option A; UI commit ed858c9). When
                # the trigger doesn't fire, no StatusSignals are yielded
                # and only the final string arrives.
                full_reply = ""
                for _item in llm_adapter.generate_response_iter(
                    context_packet,
                    style=conversational_style,
                    project_name=project_name,
                    last_session_label=last_session_label,
                    suppress_relational_lodestone=suppress_relational_lodestone,
                    temperature=_inference_temperature,
                    bare_mode=_bare_mode,
                    vision_description=_vision_description,
                    ask_first_active=_ask_first_active,
                    intent_class=_intent_class,
                    session_id=session_id,
                ):
                    if isinstance(_item, StatusSignal):
                        yield _status_event(_item.name)
                    else:
                        full_reply = _item

                # 3. Grounding check
                yield _status_event("verifying")
                # B-QUAL-004: short-circuit on empty context. Helper lives in
                # grounding_check.py for unit-testable wiring; rationale
                # documented there.
                if should_short_circuit_grounding(_retrieved_context):
                    is_grounded = False
                    unsupported = "no retrieved context to verify claims against"
                    logger.warning(
                        "[GROUNDING] empty_context_short_circuit intent=%s",
                        _intent_class,
                    )
                else:
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

                # 3.5. Deviation detection (ADR-026) - after grounding, before stream
                if not _skip_vault:
                    _prior = None
                    _buffer_turns = llm_adapter.prompt_builder.conversation_buffer.get_recent()
                    if _buffer_turns and len(_buffer_turns) >= 2:
                        _prior = _buffer_turns[-2].get("assistant")
                    threading.Thread(
                        target=_background_deviation_detection,
                        args=(full_reply, _intent_class, latest_user_message, _prior),
                        daemon=True,
                    ).start()

                # 3.6. Coaching-frame filter - post-generation, pre-stream.
                # Skip when the response is a constitutional refusal - refusal
                # text must not be rewritten or stripped by the filter. Short
                # refusals like "I can't help with that." match coaching-closing
                # patterns and get deleted, producing a blank response.
                if not _is_refusal_response(full_reply):
                    from src.llm.coaching_filter import filter_coaching_frame
                    full_reply = filter_coaching_frame(full_reply, _intent_class, _is_conversational)
                    # Post-coaching empty check - if coaching_filter
                    # stripped the entire response (e.g. short refusal
                    # matched a closing pattern), refill immediately.
                    if not full_reply or not full_reply.strip():
                        full_reply = (
                            "I had trouble generating a response to that. "
                            "Try rephrasing, or let me know what you're "
                            "actually trying to figure out."
                        )

                # B-MEM-005 / S1: response is now finalized - promote pending
                # hedge marks. Done here (not at prompt-build time) so failed
                # LLM calls or stripped-out hedges don't leave spurious marks.
                llm_adapter.prompt_builder.conversation_buffer.commit_pending_hedge()

                # 3.7. Post-gen validators (source / vision / ask-first /
                # empty-guard). Grounded streaming path - this runs before
                # the word-by-word re-stream, so the client sees the
                # validated text.
                from src.llm.post_gen_pipeline import run_post_gen_pipeline
                _postgen = run_post_gen_pipeline(
                    full_reply,
                    intent_class=_intent_class,
                    web_search_autonomous=_web_autonomous,
                    used_web_search=used_web_search,
                    used_vault=used_vault,
                    used_vision=used_vision,
                    web_items=getattr(context_packet, "web_items", None),
                    vault_sources=vault_sources,
                    vision_description=_vision_description,
                    confirmation_search_failed=_confirmation_search_failed,
                    explicit_search_request=_explicit_search,
                    ask_first_active=_ask_first_active,
                    user_message=latest_user_message,
                    memory_items=getattr(context_packet, "memory_items", None),
                    state_items=getattr(context_packet, "state_items", None),
                )
                full_reply = _postgen.reply
                # When the post-gen pipeline substituted the response
                # (ask-first or web-refusal), the vault badge is stale -
                # the substituted text didn't come from the vault.
                nonlocal _suppress_source_badges
                _suppress_source_badges = _suppress_source_badges or (
                    _postgen.ask_first_substituted or _postgen.web_refusal_substituted
                )
                if _suppress_source_badges:
                    vault_sources.clear()

                # FINAL empty guard - catches any case where coaching
                # filter, post-gen pipeline, or constitutional review
                # zeroed the response. UAT-015: blank responses must
                # never reach the client.
                if not full_reply or not full_reply.strip():
                    full_reply = (
                        "I had trouble generating a response to that. "
                        "Try rephrasing, or let me know what you're "
                        "actually trying to figure out."
                    )
                    logger.warning("[EMPTY_GUARD] Final guard fired before re-stream")

                # 4. Re-stream verified response word by word
                tokens = full_reply.split(" ")
                for i, token in enumerate(tokens):
                    text = token if i == len(tokens) - 1 else token + " "
                    yield _emit_chunk(content=text)

                # 5. Web search sources event (if applicable) - suppressed
                # when ask-first substituted (search hasn't run yet).
                if used_web_search and context_packet.web_items and not _suppress_source_badges:
                    sources = [
                        {"title": item.get("title", ""), "url": item.get("url", "")}
                        for item in context_packet.web_items
                        if item.get("url")
                    ]
                    if sources:
                        yield sse_sources(sources)

                # 6. Vault sources event (if applicable) - suppressed when
                # the post-gen pipeline substituted the response.
                if vault_sources and not _suppress_source_badges:
                    yield sse_vault_sources(vault_sources)

                # Final chunk
                yield _emit_final_chunk_and_done()

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
                    temperature=_inference_temperature,
                    bare_mode=_bare_mode,
                    vision_description=_vision_description,
                    ask_first_active=_ask_first_active,
                    intent_class=_intent_class,
                    session_id=session_id,
                ):
                    filtered = think_filter.filter(chunk)
                    if filtered:
                        accumulated.append(filtered)
                        yield _emit_chunk(content=filtered)

                # Vault sources event (if applicable)
                if vault_sources:
                    yield sse_vault_sources(vault_sources)

                # Final chunk
                yield _emit_final_chunk_and_done()

                full_reply = "".join(accumulated)
                # Coaching-frame filter - applied to accumulated text.
                # In the fast streaming path, the user has already seen
                # the raw stream. The filter cleans the version stored
                # to memory so retrieval doesn't resurface coaching patterns.
                from src.llm.coaching_filter import filter_coaching_frame
                full_reply = filter_coaching_frame(full_reply, _intent_class, _is_conversational)
                # B-MEM-005 / S1: commit pending hedge marks now that the
                # response is finalized. In the fast path the client has
                # already seen the raw stream, but the hedge accounting still
                # tracks the memory copy correctly for follow-up suppression.
                llm_adapter.prompt_builder.conversation_buffer.commit_pending_hedge()
                # Post-gen validators. In the fast path the client has
                # already seen the raw stream; this cleans the memory
                # copy so fabricated sources, vision refusals, and empty
                # replies don't re-emerge on retrieval.
                from src.llm.post_gen_pipeline import run_post_gen_pipeline
                _postgen = run_post_gen_pipeline(
                    full_reply,
                    intent_class=_intent_class,
                    web_search_autonomous=_web_autonomous,
                    used_web_search=used_web_search,
                    used_vault=used_vault,
                    used_vision=used_vision,
                    web_items=getattr(context_packet, "web_items", None),
                    vault_sources=vault_sources,
                    vision_description=_vision_description,
                    confirmation_search_failed=_confirmation_search_failed,
                    explicit_search_request=_explicit_search,
                    ask_first_active=_ask_first_active,
                    user_message=latest_user_message,
                    memory_items=getattr(context_packet, "memory_items", None),
                    state_items=getattr(context_packet, "state_items", None),
                )
                full_reply = _postgen.reply
                _post_stream_cleanup(full_reply)

        response_headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        if used_web_search and not _suppress_source_badges:
            response_headers["X-Ember-Web-Search"] = "true"
        if used_vault and not _suppress_source_badges:
            response_headers["X-Ember-Vault-Used"] = "true"
        if used_vision:
            response_headers["X-Ember-Vision-Used"] = "true"

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
        temperature=_inference_temperature,
        bare_mode=_bare_mode,
        vision_description=_vision_description,
        ask_first_active=_ask_first_active,
        intent_class=_intent_class,
    )

    # Coaching-frame filter - post-generation, pre-return.
    # Skip on refusal responses to prevent stripping.
    if not _is_refusal_response(reply):
        from src.llm.coaching_filter import filter_coaching_frame as _filter_cf
        reply = _filter_cf(reply, _intent_class, _is_conversational)

    # B-MEM-005 / S1: response is finalized - commit pending hedge marks now.
    llm_adapter.prompt_builder.conversation_buffer.commit_pending_hedge()

    # Post-gen validators (source / vision / ask-first / empty-guard).
    # Non-streaming path: runs before final JSONResponse return.
    from src.llm.post_gen_pipeline import run_post_gen_pipeline as _run_postgen
    _postgen_ns = _run_postgen(
        reply,
        intent_class=_intent_class,
        web_search_autonomous=_web_autonomous,
        used_web_search=used_web_search,
        used_vault=used_vault,
        used_vision=used_vision,
        web_items=getattr(context_packet, "web_items", None),
        vault_sources=vault_sources,
        vision_description=_vision_description,
        confirmation_search_failed=_confirmation_search_failed,
        ask_first_active=_ask_first_active,
        user_message=latest_user_message,
        memory_items=getattr(context_packet, "memory_items", None),
        state_items=getattr(context_packet, "state_items", None),
    )
    reply = _postgen_ns.reply
    if _postgen_ns.ask_first_substituted or _postgen_ns.web_refusal_substituted:
        used_vault = False
        vault_sources = []

    # B3 self-narrative class-based audit. Non-streaming path. Same
    # contract as the streaming path: sync inline, audit-only, no
    # vault read, no LLM call. See _post_stream_cleanup for streaming
    # path variant. docs/audits/b3_self_narrative_diagnosis.md.
    try:
        from src.safety.self_narrative_check import (
            check as _self_narrative_check,
            log_self_narrative_outcome,
        )
        _sn_count, _sn_indices = _self_narrative_check(reply)
        log_self_narrative_outcome(
            _sn_count, _sn_indices, intent_class=_intent_class,
        )
    except Exception as exc:
        logger.warning(
            "[SELF_NARRATIVE] Audit pass failed (non-fatal): %s", exc,
        )

    # Skip vault writes for test sessions
    if not _skip_vault:
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

    # Background state extraction - skip for test sessions to prevent eval leakage
    if not _skip_vault:
        threading.Thread(
            target=_background_state_extraction,
            args=(latest_user_message, reply),
            daemon=True,
        ).start()
        # BUG-009: resolve open_loops for declined topics
        threading.Thread(
            target=_background_topic_decline_resolution,
            args=(latest_user_message,),
            daemon=True,
        ).start()

    # Commitment detection (ADR-014) - skip for test sessions
    if not _skip_vault:
        threading.Thread(
            target=_detect_and_write_commitment,
            args=(reply, session_id),
            daemon=True,
        ).start()

    # Task detection - skip for test sessions
    if not _skip_vault:
        threading.Thread(
            target=_detect_task_in_response,
            args=(reply, session_id),
            daemon=True,
        ).start()

    # Ask-first confirmation detection - write pending_confirmation
    if not _skip_vault:
        _write_pending_confirmation(
            reply, _raw_user_message, session_id,
            existing_pending=_pending_records,
        )

    # Deviation detection - async, no latency impact (ADR-026)
    if not _skip_vault:
        _prior = None
        _buffer_turns = llm_adapter.prompt_builder.conversation_buffer.get_recent()
        if _buffer_turns and len(_buffer_turns) >= 2:
            _prior = _buffer_turns[-2].get("assistant")
        threading.Thread(
            target=_background_deviation_detection,
            args=(reply, _intent_class, latest_user_message, _prior),
            daemon=True,
        ).start()

    if _skip_vault:
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
    if used_web_search and not (_postgen_ns.ask_first_substituted or _postgen_ns.web_refusal_substituted):
        non_stream_headers["X-Ember-Web-Search"] = "true"
    if used_vault:
        non_stream_headers["X-Ember-Vault-Used"] = "true"
    if used_vision:
        non_stream_headers["X-Ember-Vision-Used"] = "true"
    return JSONResponse(content=response_body.model_dump(), headers=non_stream_headers)
