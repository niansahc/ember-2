# ADR-011: Multi-Record State Categories

**Status:** Accepted
**Date:** 2026-03-28

## Context

StateResolver currently collapses all records to one per category using
latest-wins resolution. This works correctly for single-valued categories:

- `current_focus` — a person has one current focus at a time
- `active_project` — one primary project (or the most recently noted one)
- `priority` — one top priority
- `blocker` — one active blocker
- `routine` — one routine description

But `open_loop` and `next_action` are fundamentally multi-valued. A person
has multiple open loops and multiple next actions concurrently. Collapsing
them to one loses real operational context.

This was discovered when seeding state records manually: two distinct
open_loop records were written (extraction threshold fix, model retest
scheduling), but only the latest one survived resolution. The first was
silently dropped.

## Decision

Update StateResolver to support a configurable set of multi-record
categories. `open_loop` and `next_action` will return all active records,
not just the latest. Single-record categories retain latest-wins behavior.

## Implementation Notes

- Add `MULTI_RECORD_CATEGORIES = {"open_loop", "next_action"}` to
  StateResolver or state models
- `get_current_state()` returns:
  - For single-record categories: one StateItem per category (latest wins)
  - For multi-record categories: all non-deleted records
- ContextPacket and prompt rendering already handle lists of state items —
  no changes needed there
- Cap multi-record categories at 5 items per category to prevent prompt
  bloat. If more than 5 exist, keep the 5 most recent.
- Soft-delete still works: a record with `deleted: true` in metadata is
  excluded from both single and multi-record resolution

## Consequences

- More accurate operational state — multiple open loops tracked simultaneously
- Prompt may get longer if many open loops exist, capped at 5 per category
- State extraction may produce more records over time — cleanup script
  (`scripts/cleanup_test_sessions.py` pattern) can be adapted for stale
  state records
- No breaking changes — single-record categories behave identically

## Status

Proposed, scheduled for v0.11.0.
