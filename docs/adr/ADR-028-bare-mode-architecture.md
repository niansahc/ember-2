# ADR-028: Bare Mode Architecture

**Status:** Accepted (final)
**Date:** 2026-04-13

## Context

Ember-2's response pipeline includes identity layers (nature document, lodestone injection, conversational style, relational principles) that produce a distinctive personality. Some use cases require direct, terse, retrieval-oriented responses without these layers — a "bare mode" that strips personality while preserving accuracy guarantees.

## Decision

Implement a two-layer gate for bare mode.

### Gate Structure

1. **App settings toggle** — enables the bare mode capability. Off by default.
2. **Per-conversation toggle** — available only when the capability is enabled in app settings. Does not persist across conversations.

### Optional Persist-as-Default

A "persist as default" checkbox is available only when the capability is enabled. This sets the per-conversation toggle's default state for new conversations.

### What Bare Mode Disables

- Nature document
- Lodestone injection
- Identity rules
- Conversational style rules
- Relational principles

### What Bare Mode Preserves

- Retrieval pipeline
- Context assembly
- Authority rules
- Grounding check (ADR-019)
- Constitutional review — reduced to three rules only:
  - `position_collapse`
  - `sycophancy`
  - `non_embellishment`

### Behavior

Terse, direct, retrieval-oriented. Accuracy standards unchanged. No personality, no warmth, no relational framing.

## Rationale

- Two-layer gate prevents accidental activation (settings toggle must be on first).
- Per-conversation scope prevents bare mode from leaking into conversations where personality is expected.
- Preserving retrieval and grounding ensures factual accuracy is never degraded regardless of mode.
- Reduced constitutional review keeps the three rules most relevant to factual integrity while removing personality-enforcement rules.

## Consequences

+ Users who need raw retrieval get it without personality overhead.
+ Accuracy guarantees are identical in both modes.
- Two toggles may feel heavy for power users who always want bare mode (mitigated by persist-as-default).
- Testing surface doubles — all eval scenarios must pass in both modes.