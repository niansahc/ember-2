# ADR-047: Exchange Persistence

**Status:** Accepted
**Date:** 2026-10-09
**Target:** v0.19.0
**Amends:** ADR-015 (memory tiering), ADR-026 (deviation engine), ADR-038 (pending_confirmation), ADR-040 (SSE wire contract), ADR-042 (GenerationContext)
**Related:** ADR-002 (append-only memory), ADR-021 (third-party flag), ADR-031 (vault toggle), ADR-033 (StateExtractor gating), ADR-039 (atomic JSON writes), issue #164, `CONTEXT.md`

Terms follow `CONTEXT.md`: a **Turn** is one side of a conversation, an
**Exchange** is a user turn plus everything Ember does in response to it, and an
**Exchange outcome** records how an exchange ended when it stored no assistant
turn.

## Context

A conversation's history is the set of `conversation` records carrying its
`session_id`. Before this change, those records were written in a way that lost
them silently:

- **Both turns were written after `[DONE]`.** A generation failure, a client
  disconnect, or any exception in post-stream cleanup left nothing in the
  vault. The UI worked around it: re-selecting the conversation that was
  streaming was a no-op, because a reload "would come back without it and the
  reply would vanish."
- **The write filter blocked the write itself.** `should_skip_memory` dropped
  conversation text containing a code fence, starting with `{` or `[`, or
  carrying a meta marker. Every user turn with one of Ember's `[System: ...]`
  notes started with `[`, so task, timer and "no vault content" turns were
  never stored at all.
- **The user turn stored the prompt, not the message.** Ember's notes were
  prepended, an image-only message stored the placeholder sentence the model
  received, and a confirmed web search stored the original query instead of the
  "yes" the user sent.
- **History returned the oldest 200 turns**, so a long conversation lost its
  newest exchanges on reload.
- **The pending confirmation was written after `[DONE]`** (ADR-038), the same
  window as the turns.
- **The response id was minted twice more** on the stream and non-stream paths,
  so no record could be tied to the response the client received, despite
  ADR-042's claim of one id.
- **Deviation detection ran twice** on the grounded path (issue #164), once on
  pre-coaching text.

## Decision

### 1. The record write and the index are separate

`src/memory/write_memory.py` splits into:

- `write_canonical_record()`: vault-block check, ADR-021 third-party flag,
  atomic write (ADR-039). No content filter.
- `should_index(record)`: the one filter for every derived artifact. Exchange
  outcome records never index; `should_skip_memory` rejects low-value text; eval
  fixtures index only into the test vault (issue #211).
- `index_record()`: embedding and the `memory.db` insert.

`write_memory()` keeps its signature and its behavior (the filter still blocks
the write) for every caller except the exchange recorder.

### 2. One recorder per exchange

`ExchangeRecorder` (`src/memory/exchange.py`) is created in Phase A and every
exit from the exchange goes through it:

- `record_user_turn(text, image_count)` writes the user record, then the
  conversation record if the conversation is new. The user record comes first,
  so Ember never creates an empty conversation; if the conversation-record write
  fails, the turn stays unlisted until the next message creates the record.
- `record_reply()` or `record_outcome()` finishes the exchange. The first finish
  wins under a lock; a second writes nothing and logs `[EXCHANGE] already
  finished`.
- `ensure_user_indexed()` schedules the user turn's index job once, after
  `build_context`, so an exchange cannot retrieve its own user turn.
- Index jobs run in vault-bound threads (issue #144); a failure leaves the
  canonical record in place for a rebuild.
- With vault writes off (ADR-031) or a test request, it writes nothing.

**Invariant:** every stored user turn ends in exactly one assistant turn or
exactly one exchange outcome, unless the process dies mid-exchange. A
handler-level guard records `failed` for any raise after the user-turn write
(before the stream, and on the non-stream path); `guard_sse(on_abort=...)`
records `failed` or `interrupted` for the SSE body; `vision_unavailable` records
`failed` with reason `vision_unavailable` (its canned text is not Ember's reply
and is not stored).

### 3. What the user turn stores

The text the client sent, unchanged: no `[System:` notes and no image
placeholder. An image-only message stores empty text plus `image_count`; images
are counted, never kept. The conversation title comes from the stored text, so
an image-only first message is titled "New conversation". The model still
receives the notes and the placeholder.

The client's own text is what is stored: the UI's document-upload note is part
of what the client sends and is kept.

### 4. Ordering on the grounded path (production)

1. Phase A: test flag, vault flags, project resolution, `record_user_turn`.
2. Clarification, if it claims the request: its scripted reply is the assistant
   turn, flagged `awaiting_search_content` for B2.
3. Phase B prep, B2, `build_context`, then `ensure_user_indexed()`.
4. `vision_unavailable`, if it applies: `record_outcome("failed",
   "vision_unavailable")`.
5. Generation; `_commit_delivery` on success (ADR-015, unchanged).
6. Grounding, revision, coaching, post-gen, the final empty-reply check.
7. `record_reply`, then the one deviation run (ADR-026, issue #164).
8. The pending confirmation (ADR-038).
9. Re-stream, `sources`, `vault_sources`, stop, `[DONE]`.
10. After `[DONE]`: the self-narrative audit and the extractors (state, topic
    decline, commitment, task offer), each in its own try block.

The fast path (test-only) stores the reply after its token loop and before
`vault_sources` and `[DONE]`; partial tokens are never stored. The non-stream
path stores the reply before its JSON response.

### 5. How each exchange ends

| How the exchange ends | User turn | Assistant turn | Outcome | Client receives |
|---|---|---|---|---|
| Success | yes | yes | none | the reply |
| Clarification | yes | yes (scripted) | none | the clarification |
| Generation fails in the stream | yes | none | `failed` + type | `generation_failed` frame |
| Handler raises before the stream, or non-stream generation fails | yes | none | `failed` + type | HTTP 500 |
| `vision_unavailable` | yes | none | `failed`, `vision_unavailable` | the canned vision text |
| Client disconnects before the reply is stored | yes | none | `interrupted` | n/a |
| Client disconnects after the reply is stored | yes | yes | none | part of the reply |
| User-record write fails | no | none | none | `storage_failed` frame, or HTTP 500 non-stream |
| Conversation-record write fails | yes | none | `failed`, best effort | `storage_failed` frame, or HTTP 500 non-stream |
| Reply write fails | yes | none | `failed`, best effort | `storage_failed` frame, or HTTP 500 non-stream |
| Vault off or test request | no | no | no | the reply |

The UI's stop button only stops the display; the request keeps running and the
reply is stored. Only a client disconnect interrupts an exchange.

### 6. Storage failures are loud

Ember saves a reply before sending any of it. When a write fails, the exchange
fails with the `storage_failed` error code (ADR-040 v3 amendment): "Ember
couldn't save this conversation, so the reply wasn't sent. Check that the vault
is reachable." A Phase A failure on a streaming request sends that frame, stop
and `[DONE]` instead of HTTP 500. `VaultWriteBlocked` maps to the same code.

### 7. History

`GET /v1/conversations/{session_id}` returns the newest `limit` turns in
ascending order. A user turn followed by an identical user turn (same text and
`image_count`) is a retry and is shown once; the collapse runs before `limit`
and happens at read time only. Every turn carries `image_count` (0 when none).
Exchange outcomes are `system_event` records and never appear.

### 8. One filter for every derived artifact

`should_index` decides what the live index, a `memory.db` rebuild (conversation
records), and monthly reflection (conversation records) see. History is the
only reader that sees every turn.

### 9. Derived work reads the user turn

State extraction, topic-decline resolution and deviation detection receive the
stored user turn, not the prompt with Ember's notes, so the notes cannot become
state about the user (the root cause ADR-033 names for imports). Deviation
detection runs once per exchange, right after the reply is stored, on the stored
text.

### 10. The exchange id

The exchange id is the request's `completion_id`. The stream and non-stream
re-mints are removed, so the id the client receives is the `exchange_id` on the
user turn, the assistant turn and the outcome. Other records an exchange writes
(tasks, timers, state) do not carry it yet.

## Consequences

**Positive:**
- A turn is never lost to the content filter, a failure, or a disconnect; a
  reload shows what the user saw, including code-fence replies.
- Every failed or interrupted exchange is accounted for in the vault.
- Storage problems surface immediately and accurately.
- Derived artifacts and history read the same records through one filter.

**Negative:**
- A rare disk failure now costs an already generated reply.
- Outcome records accumulate with no reader in this change.
- The vault holds both exchanges of a retry; history hides one.
- No migration (old empty conversations stay; old image-only turns keep the
  placeholder text and report `image_count` 0).

## Alternatives considered

- **Keep writing after `[DONE]` and make the cleanup more robust.** Rejected:
  the client's `[DONE]` should mean the exchange is stored.
- **Store the image placeholder.** Rejected: vault records are permanent and the
  user never sent that sentence; the display is cheap to change.
- **De-duplicate retries at write time, or send a retry header from the UI.**
  Rejected for now: both break "an exchange has exactly one user turn" or need a
  cross-repo change; the read-time collapse needs neither.
- **Always deliver the reply when a write fails.** Rejected: chatting on while
  nothing is saved is the silent loss this change removes.
- **A per-exchange flag checked at each exit instead of the recorder.**
  Rejected: seven exits, one rule; the rule belongs in one place.

## References

- `CONTEXT.md` -- Turn, Exchange, Exchange outcome, Retry, History, Conversation.
- `src/memory/exchange.py` -- `ExchangeRecorder`.
- `src/memory/write_memory.py` -- `write_canonical_record`, `should_index`, `index_record`.
- `src/api/openai_adapter.py` -- Phase A, `_complete_exchange`, the ordering above.
- `src/api/sse.py` -- `guard_sse(on_abort=...)`, `storage_failed`.
- `src/memory/session.py` -- `get_turns`.
- `tests/test_exchange_recorder.py`, `tests/test_exchange_persistence.py`.
