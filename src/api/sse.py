"""
src/api/sse.py

Canonical serializer for the /v1/chat/completions streaming (SSE) wire contract.

ADR-040 documents the frozen contract; this module is its single producer, so
the wire format has one source of truth and the golden-frame tests
(tests/test_sse_contract.py) can pin it byte-for-byte.

Frame families (see ADR-040 for the full schema):
  - chat.completion.chunk frames (content / terminal) via sse_chunk()
  - Ember typed frames (NOT OpenAI; top-level `type`):
      {type, content}  status signal      via sse_status()
      {type, sources}  web citations       via sse_sources()
      {type, sources}  vault citations     via sse_vault_sources()
      {type, code, message}  generation or storage failure via sse_error() (v3)
  - the [DONE] terminator via sse_done()

guard_sse() wraps every StreamingResponse body: an uncaught exception becomes
the error frame + stop + [DONE] instead of a silently truncated stream.

B-SSE-001 (ADR-040 contract v2): status is a top-level typed frame,
{"type": "status", "content": "<value>"}, a sibling of the sources /
vault_sources frames. It is emitted by sse_status(), NOT as a
choices[0].delta.status chunk. The v1 shape carried status inside the chunk
delta, which the UI parser (which reads a top-level frame with a `content`
field) silently dropped; moving status to a top-level frame broke no working
consumer. Any further change to this shape must follow the ADR-040 change
procedure (backend + UI + ADR version bump + golden tests in lockstep).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

_logger = logging.getLogger("ember.openai_adapter")

# The model id reported in every chunk. Mirrors EMBER_MODEL_ID in
# openai_adapter; duplicated here to keep this module dependency-free.
EMBER_MODEL_ID = "ember-2"


def _chunk(completion_id: str, delta: dict, finish_reason: str | None) -> str:
    """Build one chat.completion.chunk SSE line with the canonical key order."""
    return "data: " + json.dumps({
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": EMBER_MODEL_ID,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }) + "\n\n"


def sse_chunk(
    completion_id: str,
    *,
    content: str | None = None,
    finish_reason: str | None = None,
) -> str:
    """One chat.completion.chunk SSE frame.

    delta resolves to:
      - {"content": content} when content is not None (including the initial
        content="" typing indicator), else
      - {}                   (terminal frame; pair with finish_reason="stop").

    Status signals are NOT chunks -- see sse_status().
    """
    if content is not None:
        delta: dict = {"content": content}
    else:
        delta = {}
    return _chunk(completion_id, delta, finish_reason)


def sse_status(value: str) -> str:
    """Status signal frame: {"type": "status", "content": "<value>"}.

    A top-level typed frame (ADR-040 contract v2, B-SSE-001), sibling of the
    sources / vault_sources frames. `value` is one of exactly: searching,
    review_pending, review_complete, verifying, refining. The phase is carried
    in `content` -- the field the UI parser reads.
    """
    return "data: " + json.dumps({"type": "status", "content": value}) + "\n\n"


def sse_sources(sources: list[Any]) -> str:
    """Web-search citation frame: {"type": "sources", "sources": [...]}."""
    return "data: " + json.dumps({"type": "sources", "sources": sources}) + "\n\n"


def sse_vault_sources(sources: list[Any]) -> str:
    """Vault citation frame: {"type": "vault_sources", "sources": [...]}."""
    return "data: " + json.dumps({"type": "vault_sources", "sources": sources}) + "\n\n"


GENERATION_FAILED_CODE = "generation_failed"
GENERATION_FAILED_MESSAGE = (
    "Ember couldn't generate a reply. Check that the model server is reachable."
)

# ADR-040 v3 amendment (ADR-047): the exchange could not be saved, so the reply
# was not sent. A second value of the same `code` field, no new field.
STORAGE_FAILED_CODE = "storage_failed"
STORAGE_FAILED_MESSAGE = (
    "Ember couldn't save this conversation, so the reply wasn't sent. "
    "Check that the vault is reachable."
)

_ERROR_MESSAGES = {
    GENERATION_FAILED_CODE: GENERATION_FAILED_MESSAGE,
    STORAGE_FAILED_CODE: STORAGE_FAILED_MESSAGE,
}


def sse_error(code: str = GENERATION_FAILED_CODE) -> str:
    """Error frame: {"type": "error", "code": "<str>", "message": "<str>"}.

    ADR-040 contract v3. Fixed text per code only: exception detail goes to
    the application log, never onto the wire. The UI renders `message` and
    never reads `code`.
    """
    return "data: " + json.dumps({
        "type": "error",
        "code": code,
        "message": _ERROR_MESSAGES[code],
    }) + "\n\n"


def error_code_for(exc: BaseException) -> str:
    """The error code an exception surfaces as.

    An exception class opts in to a code with an `sse_error_code` attribute
    (ExchangeStorageError declares storage_failed). Everything else, and any
    unknown code, is generation_failed. Attribute-based so this module stays
    free of imports from the rest of the app.
    """
    code = getattr(exc, "sse_error_code", GENERATION_FAILED_CODE)
    return code if code in _ERROR_MESSAGES else GENERATION_FAILED_CODE


def sse_done() -> str:
    """The stream terminator (literal, not JSON)."""
    return "data: [DONE]\n\n"


def _notify_abort(on_abort, outcome: str, reason: str | None) -> None:
    """Call on_abort without letting its failure replace the stream's own.

    on_abort is synchronous by contract: it can run inside a cancelled scope,
    where any await would raise CancelledError again.
    """
    if on_abort is None:
        return
    try:
        on_abort(outcome, reason)
    except Exception as exc:  # noqa: BLE001
        _logger.error("[SSE] on_abort failed: %s", type(exc).__name__)


async def guard_sse(body, completion_id: str, on_abort=None):
    """Wrap a StreamingResponse body so no exception ends the stream silently.

    Headers (HTTP 200) are flushed before the body runs, so an exception that
    escapes the body truncates the stream with no error and no [DONE]. Every
    StreamingResponse in src/api wraps its body in this guard.

    - Exception before the terminal frames: log "[GENERATION] failed: <Type>"
      (or "[STORAGE] failed: <Type>" for an exception that declares the
      storage_failed code) with the traceback, call on_abort("failed",
      "<Type>"), then yield the error frame with that code (ADR-040 v3), a
      stop chunk and [DONE], once. Exception text never reaches the wire.
    - CancelledError / GeneratorExit before the terminal frames (the client
      disconnected): call on_abort("interrupted", None), then re-raise.
    - Anything after the body already yielded [DONE] (post-stream cleanup):
      log "[SSE] post-terminal failure: <Type>" and yield nothing; the client
      has its complete stream, and on_abort is not called.
    - Only Exception is caught; cancellation always propagates, and an async
      inner body is closed on the way out.

    on_abort(outcome, reason) lets the caller end its exchange with an
    exchange outcome (ADR-047). It must be synchronous; see _notify_abort.

    Sync bodies are iterated in the threadpool, as Starlette does for them,
    so blocking generation never runs on the event loop.
    """
    import asyncio

    from starlette.concurrency import iterate_in_threadpool

    iterator = body if hasattr(body, "__aiter__") else iterate_in_threadpool(body)
    done_frame = sse_done()
    terminated = False
    try:
        async for frame in iterator:
            if done_frame in frame:
                terminated = True
            yield frame
    except Exception as exc:
        if terminated:
            _logger.error(
                "[SSE] post-terminal failure: %s", type(exc).__name__, exc_info=exc,
            )
            return
        code = error_code_for(exc)
        # A wrapping exception can name its underlying cause (cause_type);
        # that type, not the wrapper's, is what the log and on_abort report.
        type_name = getattr(exc, "cause_type", None) or type(exc).__name__
        _logger.error(
            "[%s] failed: %s",
            "STORAGE" if code == STORAGE_FAILED_CODE else "GENERATION",
            type_name,
            exc_info=exc,
        )
        _notify_abort(on_abort, "failed", type_name)
        yield sse_error(code) + sse_chunk(completion_id, finish_reason="stop") + done_frame
    except (asyncio.CancelledError, GeneratorExit):
        if not terminated:
            _logger.warning("[SSE] client disconnected before [DONE]")
            _notify_abort(on_abort, "interrupted", None)
        raise
    finally:
        # Async bodies only: a sync body may still be mid-next() in a worker
        # thread on cancellation, and closing it from here would raise.
        close = getattr(body, "aclose", None)
        if close is not None:
            await close()
