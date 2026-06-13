"""
src/api/sse.py

Canonical serializer for the /v1/chat/completions streaming (SSE) wire contract.

ADR-040 documents the frozen contract; this module is its single producer, so
the wire format has one source of truth and the golden-frame tests
(tests/test_sse_contract.py) can pin it byte-for-byte.

Frame families (see ADR-040 for the full schema):
  - chat.completion.chunk frames (content / status / terminal) via sse_chunk()
  - Ember citation frames {type, sources} via sse_sources() / sse_vault_sources()
  - the [DONE] terminator via sse_done()

B-SSE-001: status is shipped INSIDE the chunk as choices[0].delta.status, which
is what the backend has always emitted. That historical shape is preserved here
deliberately. The UI parser currently expects a top-level {type: "status"} and
therefore drops status events; reconciling that mismatch is a separate
coordinated change under the ADR-040 change procedure, NOT part of this
byte-for-byte serializer consolidation.
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
    status: str | None = None,
    finish_reason: str | None = None,
) -> str:
    """One chat.completion.chunk SSE frame.

    delta resolves to:
      - {"status": status}  when status is set (status signal frame), else
      - {"content": content} when content is not None (including the initial
        content="" typing indicator), else
      - {}                   (terminal frame; pair with finish_reason="stop").
    """
    if status is not None:
        delta: dict = {"status": status}
    elif content is not None:
        delta = {"content": content}
    else:
        delta = {}
    return _chunk(completion_id, delta, finish_reason)


def sse_sources(sources: list[Any]) -> str:
    """Web-search citation frame: {"type": "sources", "sources": [...]}."""
    return "data: " + json.dumps({"type": "sources", "sources": sources}) + "\n\n"


def sse_vault_sources(sources: list[Any]) -> str:
    """Vault citation frame: {"type": "vault_sources", "sources": [...]}."""
    return "data: " + json.dumps({"type": "vault_sources", "sources": sources}) + "\n\n"


def sse_done() -> str:
    """The stream terminator (literal, not JSON)."""
    return "data: [DONE]\n\n"
