# ADR-031: Per-Conversation Vault Toggle

**Status:** Accepted (final)
**Date:** 2026-04-13

## Context

Some conversations do not need vault access — quick factual lookups, brainstorming, or discussions where the user does not want Ember reading or writing memory. Currently, vault access is always active. There is no way to have a stateless LLM conversation within Ember.

Bare mode (ADR-028) disables personality layers but keeps vault active. This is a distinct concern: vault toggle controls whether memory is read or written, not whether personality is applied. A user may want bare mode with vault on (terse retrieval) or normal mode with vault off (personality without memory).

## Decision

Implement a two-layer gate for per-conversation vault access.

### Gate Structure

1. **Global setting** — enables the vault toggle capability. Off by default. Lives in preferences.
2. **Per-conversation toggle** — available only when the global setting allows it. Defaults to vault ON for every new conversation.

### What "Vault Off" Means

When vault is toggled off for a conversation, the following are ALL disabled:

- Vault reads (no retrieval, no context assembly from memory)
- Vault writes (no conversation storage, no memory creation)
- Task generation (tasks are vault-backed)
- State writes (state is vault-backed)
- Reflection triggers (reflections are vault-backed)

The conversation is fully stateless. The LLM operates with only the system prompt and the current conversation buffer. Nothing persists after the conversation ends.

### What "Vault Off" Does NOT Mean

- Retrieval is NOT selectively disabled — vault off means completely off. There is no "read but don't write" mode.
- Constitutional review still applies — safety governance is independent of vault access.
- Identity layers still apply (unless bare mode is also enabled) — personality is independent of vault access.

### Default Behavior

Vault is always ON by default. The per-conversation toggle defaults to ON. The user must explicitly toggle vault off per conversation. There is no persist-as-default for vault off — every new conversation starts with vault on.

### Relationship to Bare Mode

| Mode | Personality | Vault | Use case |
|------|------------|-------|----------|
| Normal | ON | ON | Default Ember experience |
| Bare mode | OFF | ON | Terse retrieval-oriented responses |
| Vault off | ON | OFF | Stateless conversation with Ember's personality |
| Bare + vault off | OFF | OFF | Raw stateless LLM |

These are orthogonal toggles. Both use the same two-layer gate pattern (global enable + per-conversation toggle).

## Rationale

- Two-layer gate prevents accidental vault-off conversations (global must be enabled first).
- All-or-nothing vault access is simpler than selective read/write controls and prevents inconsistent state (e.g., writing without reading could create context-free records).
- Default vault ON ensures memory continuity is never accidentally broken.
- No persist-as-default prevents users from accidentally running weeks of conversations without vault access.

## Consequences

+ Users get a true stateless LLM mode when they need it.
+ Privacy-sensitive conversations can be fully ephemeral.
+ Clear separation from bare mode — two orthogonal concerns.
- All-or-nothing may feel heavy for users who want "read but don't write" — this is intentionally excluded to prevent inconsistent vault state.
- Per-conversation toggle resets on every new conversation — by design, not a limitation.
