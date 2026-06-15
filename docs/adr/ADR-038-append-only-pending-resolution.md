# ADR-038: Append-Only Derived Resolution for pending_confirmation

**Status:** Accepted
**Date:** 2026-06-12
**Target:** v0.18.1

## Context

The state layer is append-only by CLAUDE.md non-negotiable Rule 3: records are never overwritten in place; every change writes a new artifact. `StateService.mark_resolved()` violated this. It scanned the state directory, found the file whose `id` matched, and rewrote it in place via `f.write_text(...)` to set `metadata.resolved = True`. Its docstring acknowledged the violation ("same pattern as soft-delete").

`mark_resolved` was used for exactly one category, `pending_confirmation` (the ask-first "want me to search?" flow), via `_resolve_original_pending` in `src/api/openai_adapter.py`. Resolving the original pending is load-bearing: without it, `_check_pending_confirmation` re-finds the same pending on every subsequent turn and the confirmation prompt loops forever.

A naive "just write a new resolution record instead" does not work on its own, because nothing in the read path honored such a record:

- The two consumers (`_check_pending_confirmation` and the `_write_pending_confirmation` duplicate-write guard) decided "is this pending active?" by reading each record's own `metadata.resolved` flag. Under append-only the original keeps `resolved=False`, so both would treat a resolved pending as active again -- reopening the infinite loop and suppressing legitimate re-offers.
- `StateResolver.get_current_state()` surfaced `pending_confirmation` as a "current state" item (single-record, staleness-exempt) and also skipped only by the per-record `resolved` flag. Under append-only it would begin leaking resolved (even stale) pendings into the prompt.

A related latent gap was discovered during this work and filed separately as **B-STATE-001** in `docs/KNOWN_ISSUES.md`: `resolve_open_loops_by_topic` already writes append-only resolution records carrying `metadata.original_id`, but `original_id` is read nowhere, so the original open_loop is not actually suppressed by the resolver. That gap is **out of scope** here; A2 is scoped to `pending_confirmation` only.

## Decision

Resolution for `pending_confirmation` becomes **append-only and derived**.

1. **Append-only resolution.** `StateService.resolve_record(record_id, *, resolution=None)` appends a new resolution tombstone record of the same category carrying `metadata.resolved = True` and `metadata.original_id = record_id`. The original record file is never modified. The call is idempotent: if the record is already resolved it writes no duplicate tombstone. `mark_resolved` is removed.

2. **Derived resolved-set.** `StateService.resolved_ids(records)` is the single canonical definition of "what is resolved": the union of (a) ids of records that carry their own `metadata.resolved = True` (back-compat for records already mutated in place by the old code path) and (b) every `metadata.original_id` value present (the tombstones). A record is active iff `record.id not in resolved_ids(records)`. Both `openai_adapter` consumers use this helper instead of reading the raw flag.

3. **Resolver carve-out.** `StateResolver.get_current_state()` excludes `pending_confirmation` from surfaced items entirely (like `timer`). It is internal ask-first control flow consumed directly by the chat endpoint and never belongs in the prompt. This both fixes a real prompt-hygiene issue and removes the append-only leak surface, so the resolver's general resolution logic for real state categories is left untouched.

Scope is `pending_confirmation` only. `open_loop` and `resolve_open_loops_by_topic` are unchanged; B-STATE-001 remains open.

## Consequences

- **No in-place mutation of canonical state records.** Rule 3 holds for pending resolution. Crash-safety improves: an append never tears an existing file.
- **Back-compat without migration.** Vaults that already contain in-place-resolved pendings (from the old `mark_resolved`) are still treated as resolved via the flag branch of `resolved_ids`. No rebuild required.
- **Append-only accumulation.** Each resolution now writes a tombstone, so `pending_confirmation` records accumulate at roughly 2x the prior rate and are never compacted. `read_all()` already scans every state file per turn, so this is a marginal cost on an already-O(all-state-files) read. Acceptable for a single-user vault; a compaction/archival pass is a possible future enhancement, not part of this change.
- **One definition of "resolved."** Both consumers and the idempotency guard share `resolved_ids`, removing the per-site flag-reading that caused the original drift.
- **B-STATE-001 is not addressed.** The shared `resolved_ids` helper is deliberately NOT wired into the resolver's `open_loop` path, so the open_loop `original_id` gap persists exactly as filed. Closing it (or removing `original_id` and relying on `ConversationBuffer`) is a separate future workstream.
- **A future full event-sourced resolution** (deriving resolution for all categories via `resolved_ids` in the resolver) remains available; this ADR is the first, contained step toward it.

## References

- `docs/adr/ADR-011-multi-record-state-categories.md` -- state resolution rules this amends for `pending_confirmation`.
- `docs/KNOWN_ISSUES.md` -- B-STATE-001 (open_loop suppression gap, out of scope here).
- `src/state/state_service.py` -- `resolved_ids`, `resolve_record` (replaces `mark_resolved`).
- `src/state/state_resolver.py` -- `pending_confirmation` exclusion in `get_current_state`.
- `src/api/openai_adapter.py` -- `_resolve_original_pending`, `_check_pending_confirmation`, `_write_pending_confirmation` derived-resolution sites.
- CLAUDE.md -- Core Architectural Rule 3 (append-only memory).
