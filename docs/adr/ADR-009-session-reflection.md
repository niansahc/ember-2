# ADR-009: Session Reflection Mode

**Status:** Proposed
**Date:** 2026-03-28

## Context

Daily and weekly reflections exist and work well for capturing patterns
over time. But there's no mechanism to capture what happened in a single
work session before context is lost to buffer compression or session end.

A user who works with Ember for two hours on a complex problem, then
closes the browser, loses the nuance of that session. The daily reflection
picks up fragments but misses the arc.

## Decision

Add a session reflection mode as a distinct type:

- **Trigger:** Manual via `POST /reflect/session` or automatic at session end
- **Input:** Conversation buffer contents (not vault search — the buffer
  has the full recent context that hasn't been compressed yet)
- **Output:** Stored as derived memory with `type="reflection"`,
  `cadence="session"`, and `session_id` reference
- **Scope:** Captures the narrative of a single session — what was worked
  on, what decisions were made, what's left open

## Consequences

- Better session continuity across days
- More granular data for weekly synthesis (sessions feed into weekly reflection)
- Users can explicitly "save" a session's context before closing
- Session reflections are searchable and retrievable like any other memory

## Implementation

- New endpoint: `POST /reflect/session` (optional `session_id` parameter)
- Uses conversation buffer as primary input, not `MemoryService.read()`
- LLM call to generate session summary (same pattern as buffer compression
  but more narrative, less mechanical)
- Stored in `vault/memory/reflection/` with session metadata

## Status

Scheduled for v0.12.0 alongside the task layer.
