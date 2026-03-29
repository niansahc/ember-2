# ADR-012: UI Session Security

**Status:** Proposed
**Date:** 2026-03-28

## Context

Ember-2's current security model protects the API (key auth, Tailscale
binding, rate limiting) and the data at rest (BitLocker). But the UI
itself has no authentication layer. Anyone who can reach the URL can
use Ember.

This is acceptable for a single-user local-only setup but becomes a
meaningful gap as soon as Tailscale enables remote access from other
devices, or when multi-user support is added.

## Decision

Implement UI session security in three phases:

### Phase 1 (v0.11.0): Vault Path Masking

Display vault location as masked text with reveal-on-click (auto-re-masks
after 10 seconds) and copy-to-clipboard without display. Open folder
button remains. No path visible by default.

Minimal friction, meaningful privacy improvement for casual screen exposure.

### Phase 2 (v0.12.0 or v0.13.0): Local PIN/Passphrase Lock

A simple local passphrase that locks the Ember UI after inactivity or on
demand. Hash stored locally. Prompt on first load and after configurable
inactivity timeout. No account system, no cloud auth. Like unlocking a
phone.

Meaningful security for Tailscale remote access scenarios.

### Phase 3 (post-v0.15.0): Full Auth Layer for Multi-User

Per-user sessions, per-user vault isolation, proper login flow. Required
before multi-user deployment. See TDD section 37.

## Consequences

- Phase 1: minimal friction, meaningful privacy improvement for casual
  screen exposure
- Phase 2: meaningful security for Tailscale remote access scenarios
- Phase 3: prerequisite for multi-user deployment

## Open Questions

- Phase 2: where is the passphrase hash stored? (keyring is the right
  answer, consistent with API key storage)
- Phase 2: what is the inactivity timeout default? (15 minutes suggested)
- Phase 2: what happens if the user forgets the passphrase? (recovery
  via API key verification?)
