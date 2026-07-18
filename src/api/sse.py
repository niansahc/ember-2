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
  - the [DONE] terminator via sse_done()

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
import time
from typing import Any

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


def sse_done() -> str:
    """The stream terminator (literal, not JSON)."""
    return "data: [DONE]\n\n"
