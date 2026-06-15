# ADR-040: Streaming SSE Wire Contract for /v1/chat/completions

**Status:** Accepted
**Date:** 2026-06-13
**Target:** v0.18.1
**Contract version:** 1

## Context

`POST /v1/chat/completions` with `stream=True` returns Server-Sent Events. The
backend **serializes** the frames (`src/api/sse.py`, used by
`src/api/openai_adapter.py`); the Ember UI **parses** them
(`ember-2-ui/src/api/ember.js`). Two repositories, two languages, no shared
code is possible. Until now the wire format lived implicitly in the serializers
and was hand-synced in the UI parser -- which already produced one silent break
(status events; see B-SSE-001 below).

This ADR is the single canonical definition of that wire format. Issue #93
PR (a) consolidates the backend serializer into `src/api/sse.py` and pins it to
this contract with byte-for-byte golden tests (`tests/test_sse_contract.py`).

## Decision -- the contract

Every frame is one SSE line: `data: <payload>\n\n`. Response `media_type` is
`text/event-stream`.

### Family 1 -- OpenAI-compatible `chat.completion.chunk`

```json
{"id": "chatcmpl-<hex>", "object": "chat.completion.chunk", "created": <unix int>,
 "model": "ember-2", "choices": [{"index": 0, "delta": <delta>, "finish_reason": <null | "stop">}]}
```

Key order is fixed: `id, object, created, model, choices`; and within a choice:
`index, delta, finish_reason`. `created` is the only non-deterministic field
(wall-clock seconds). `delta` has exactly three shapes:

- **content** -- `{"content": "<text>"}`, `finish_reason: null`. The initial
  typing indicator is a content frame with `content == ""`.
- **status** -- `{"status": "<value>"}`, `finish_reason: null`. The status
  value is one of exactly: `searching`, `review_pending`, `review_complete`,
  `verifying`, `refining`. (`searching`/`verifying`/`refining` are emitted by
  `openai_adapter`; `review_pending`/`review_complete` are `StatusSignal` names
  from `src/llm/adapter.py`, the ADR-036 review signals, emitted on the wire
  today.)
- **terminal** -- `{}`, `finish_reason: "stop"`.

### Family 2 -- Ember citation frames (NOT OpenAI; top-level `type`)

- **web sources** -- `{"type": "sources", "sources": [{"title": "<str>", "url": "<str>"}, ...]}`
- **vault sources** -- `{"type": "vault_sources", "sources": [{"type": "<state|conversation|...>", "timestamp": "<iso>", "summary": "<str>"}, ...]}`

### Terminator

`data: [DONE]\n\n` (literal text, not JSON).

### Per-turn frame ordering

Optional status frames -> initial `content: ""` -> content tokens ->
[optional web `sources`] -> [optional `vault_sources`] -> terminal `stop` ->
`[DONE]`. Canned early-return paths emit a single content frame -> `stop` ->
`[DONE]` (no status or sources).

## Known discrepancy (B-SSE-001)

The backend ships status as `choices[0].delta.status` (a Family-1 delta),
documented above as the actual current contract. The UI parser
(`ember.js`) instead checks for a **top-level** `{"type": "status"}` frame and
therefore silently drops every status event today. This contract freezes the
**current backend shape** (`delta.status`); the golden tests assert it as-is.
Reconciling the mismatch (the UI reading `delta.status`, or the backend moving
status to a top-level frame) is a behavior change executed under the change
procedure below, tracked as B-SSE-001 in KNOWN_ISSUES. It was deliberately NOT
folded into the byte-for-byte serializer consolidation.

## Change procedure (the unfreeze rule)

Any change to an existing event shape, or any new event type, MUST be a single
coordinated change set that does all four:

1. Update the backend serializer (`src/api/sse.py`).
2. Update the UI parser (`ember-2-ui/src/api/ember.js`).
3. Update this ADR and bump **Contract version** above.
4. Update the backend golden-frame tests (`tests/test_sse_contract.py`).

"Frozen" means no event-shape change lands in one repository without the
matching change in the other and a contract-version bump. A backend-only or
UI-only event-shape change is a contract violation -- it is exactly the silent
break this ADR exists to prevent. The B-SSE-001 status reconciliation will be
the first worked example of this procedure.

## Consequences

- One canonical source for the wire format; `src/api/sse.py` is its only
  producer and the golden tests pin it byte-for-byte.
- The status mismatch is now visible and fixable via a defined procedure rather
  than rotting silently.
- Cross-repo coordination is required only when the contract changes, not for
  internal refactors of either the serializer or the parser, provided the wire
  format is unchanged.

## References

- `src/api/sse.py` -- the serializer (single producer).
- `tests/test_sse_contract.py` -- golden-frame tests (frozen to this contract).
- `ember-2-ui/src/api/ember.js` -- the UI parser (consumer).
- `docs/adr/ADR-036-fast-streaming-review-signal.md` -- defers the richer
  review-event protocol; its `review_pending`/`review_complete` status values
  are pinned here.
- `docs/KNOWN_ISSUES.md` -- B-SSE-001 (status shape mismatch).
- Issue #93 -- the chat_completions decomposition this PR (a) is the first step of.
