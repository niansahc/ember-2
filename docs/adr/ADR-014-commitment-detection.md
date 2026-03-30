# ADR-014: Conversational Commitment Detection and State Persistence

**Status:** Accepted
**Date:** 2026-03-30

## Context

Ember makes commitments during conversation -- "I'll walk you through this step by step," "here's your order for the day" -- and then fails to honor them in subsequent turns. This is not a model failure. It is an architectural gap: commitments exist only in conversation history, which models drop, truncate, or misread across turns. No model, local or cloud, should be relied upon to track its own prior statements through conversation history alone.

The state layer already exists for exactly this purpose. The gap is that nothing writes to it when Ember makes a commitment.

See also ADR-010 (semantic trigger pattern applied to input safety). This ADR applies the same detection pattern to Ember's output, with a different action.

## Decision

When Ember makes a commitment in her response, detect it post-generation and write an `open_loop` state record immediately. That record is then injected into every subsequent request via the existing context assembly pipeline (TDD section 14.3), making the commitment visible to the model without relying on conversation history.

Detection runs on Ember's draft response, after generation, before the response is returned. If a commitment is detected, a state record is written before the response reaches the user.

## Implementation

1. **Commitment detector** -- post-generation step that evaluates Ember's draft response for commitment language. Initial implementation: semantic similarity against commitment pattern embeddings. Threshold-gated to avoid false positives on casual language.

2. **State write** -- on positive detection, write `open_loop` record via `StateService.write()`. Record text is the commitment extracted or summarized from the response. Source: `commitment_detector`.

3. **Resolution** -- when Ember fulfills a commitment, the open loop should be resolved. Resolution via append-only: write a new `open_loop` record with `metadata.resolved: true`. StateResolver treats the latest record as current truth.

4. **Context injection** -- no new work needed. Active state records including `open_loop` are already injected into context per TDD 14.3.

5. **Evaluation before ship** -- a labeled benchmark set (target ~25 responses: genuine commitments, casual non-commitments, edge cases) with a threshold sweep measuring precision and recall. Minimum bar before v0.12.0 ships: precision > 0.85. Start conservative -- high threshold, low recall -- and loosen based on real vault data. An eval script lives alongside the detector.

## Rationale

Commitment tracking belongs in state, not conversation history. State is designed for current operational truth. `open_loop` is the correct category -- "something unresolved that needs follow-up" is exactly what a commitment is.

This fix is model-agnostic. Haiku, Sonnet, qwen3, any future model gets the commitment handed to it in the system prompt. No model is trusted to remember its own prior statements.

## Consequences

- Ember can be held to what she says across turns and sessions
- Existing state infrastructure used without schema changes
- Resolution requires a second write -- minor overhead, consistent with append-only pattern
- False positive tuning is critical: noise in state means Ember is handed commitments she never made. Evaluation is required before this ships.
- Resolved `open_loop` records are good candidates for warm/cold archive when memory tiering lands (v0.13.0). The `resolved: true` flag should be treated as a deprioritization signal by the tiering system.
- Commits and resolutions are auditable in the vault

## Alternatives Considered

| Alternative | Why rejected |
|---|---|
| Rely on conversation history | Model-dependent, proven to fail |
| New `commitment` record type | `open_loop` already covers this semantically, no need to expand taxonomy |
| Keyword matching for detection | Brittle -- same reason ADR-010 moves to semantic |
| Write commitment only on user request | Too manual, defeats the purpose |

## Open Questions

- What is the right precision/recall tradeoff for the initial threshold? Start conservative and loosen, or calibrate on real data first?
- Should the eval script live in `tools/` alongside `eval_retrieval.py`?
- When memory tiering ships, should resolved loops auto-archive, or require explicit user action?
