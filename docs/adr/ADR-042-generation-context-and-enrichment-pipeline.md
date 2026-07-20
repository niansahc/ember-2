# ADR-042: GenerationContext and the Two-Phase Enrichment Pipeline

**Status:** Accepted
**Date:** 2026-07-20
**Target:** v0.18.1
**Related:** ADR-041 (PreGeneration terminal router), ADR-040 (SSE wire contract), issue #93 (decompose `chat_completions`)

## Context

Issue #93 decomposes `chat_completions` in three sequenced PRs. PR (a) froze the
SSE wire contract (ADR-040); PR (b) extracted the pre-enrichment terminal router
(ADR-041). This ADR is **PR (c)**, the final step: introduce the per-request
context object the earlier PRs deferred, migrate the clarification short-circuit
onto it, and extract the request-enrichment work out of the endpoint body.

After PR (b), the middle of `chat_completions` was still a long run of inline
work: resolve `is_test` / vault flags / `project_id`, ensure the session, then a
bare-marker clarification short-circuit, then message-mutating prep
(pending-confirmation, task creation, timer detection) that rewrites the user
message at several sites before generation. All of it depends on
enrichment-resolved values and shares mutable locals.

## Decision

### Two carriers: a frozen context and a mutable working state

- **`GenerationContext`** (frozen) carries the enrichment-resolved identity /
  routing values that are computed once and never change: `session_id`,
  `project_id`, `project_name`, `is_test`, `vault_enabled`, `skip_vault`,
  `completion_id`, `stream`, the memoized query `policy`, and `raw_user_message`
  (the clean pre-prefix snapshot).
- **`GenerationWork`** (mutable) carries the evolving user message (rewritten with
  system prefixes by the prep builders) and the values those builders derive
  (`confirmation_web_items`, `confirmation_confirmed`, `confirmation_search_failed`,
  `pending_records`; `raw_message` stays the clean snapshot).

Why split rather than one object: PR (b)'s frozen `RouterContext` gave a
*structural* no-mutation guarantee that made interceptor inputs tamper-proof. A
prep pipeline is, by nature, a mutation flow. Freezing the values that are truly
constant (and that the clarification terminal reads) preserves that guarantee
where it matters, while a separate mutable carrier is honest about the message
rewriting that genuinely happens. Both live in `src/api/pregeneration.py`
alongside `RouterContext`; the module stays free of domain logic and of any
runtime import from `openai_adapter`.

### Two-phase enrichment, clarification in between

The endpoint body becomes:

```
normalize (messages, ### Task guard, image placeholder, session_id)
PreGenerationRouter[RouterContext].run   # empty / override / onboarding (PR b)
build GenerationContext                   # Phase A value builders
PreGenerationRouter[GenerationContext].run   # clarification (migrated)
Phase B builders over GenerationWork      # confirmation, task, timer
generation handler                        # context build, vision, intent, generate
```

Clarification runs **after** Phase A (it needs the resolved session/project/vault
values to write its two conversation turns) and **before** the Phase B prep
builders. That ordering is load-bearing, not cosmetic: in the pre-refactor code
clarification early-returned before confirmation/task/timer ever ran, so a
bare-marker turn ("google please") never triggered their side effects (task
records, timer state, pending-confirmation resolution). Running the prep builders
first and clarification last would fire those writes and then discard the work --
a behavior change. Phase A -> clarification -> Phase B preserves the original
side-effect profile exactly.

### Generic router over context type

`PreGenerationRouter` is made generic over its context type `C`. Two instances
run: `PreGenerationRouter[RouterContext]` (the PR b terminals) and
`PreGenerationRouter[GenerationContext]` (clarification). One mechanism, one
`TerminalReply` type, one `early_return_response` funnel, one `[EARLY-RETURN]`
log format. Clarification is the only enrichment-*dependent* terminal; it may
perform side effects (it writes the two turns) but reads only the frozen context
and never mutates it, so PR (b)'s contract still holds -- terminal, so nothing
downstream consumes those writes on the same turn.

### Unified completion id; memoized policy (and its limit)

- One `completion_id` is minted per request and carried from the `RouterContext`
  into the `GenerationContext`, so the pre-router terminals, the clarification
  terminal, and the final generation response all share one id. The separate
  `_clarification_id` is gone.
- `GenerationContext.policy` memoizes the `classify_query` call on the **raw**
  message. The clarification terminal consumes it instead of re-classifying.
  **This memoization does not extend to the downstream "early"/"late" policy**:
  those classify the message *after* the Phase B prep builders have prefixed it
  with `[System: ...]` notes, which is a different string. Collapsing them onto
  `GenerationContext.policy` would classify the raw message instead of the
  prefixed one and change routing. They remain separate classifications by design.

### Scoped deviation: what stays inline

The plan considered extracting the B2 next-turn dispatch and the relational-
intensity suppression into Phase B builders as well. They stay inline in the
generation handler: both are retrieval-gating policy (they set the classification
/ lodestone-suppression that feed context assembly), not message prep, and they
are entangled with the context-build branch. Extracting them would be an awkward,
higher-risk move with no clean seam. The cleanly separable, message-mutating prep
-- pending-confirmation, task, timer -- is what became builders over
`GenerationWork`.

### Source encoding note

The timer stop/check notes contain an em dash. The builders encode it as the
`\u2014` escape so the source stays ASCII (CLAUDE.md) while the runtime prompt
string is byte-identical to the pre-refactor note. A unit test asserts the
runtime string carries U+2014.

## Consequences

- `chat_completions` reads as a sequence of named phases; the enrichment work is
  in small, unit-testable builders (`_build_generation_context`,
  `_apply_confirmation`, `_apply_tasks`, `_apply_timers`) beside their
  dependencies. No symbols moved, so existing patch-based tests keep working.
- The two-stage router pattern generalizes cleanly; a future enrichment-dependent
  terminal drops into `PreGenerationRouter[GenerationContext]`.
- Behavior is preserved end-to-end: the clarification write/response contract,
  the confirmation/task/timer side effects and message prefixes, the
  `_raw_user_message` snapshot semantics, and the A1 stream-vs-JSON invariant are
  all unchanged. Verified by the existing endpoint integration suite plus new
  builder-level unit tests.
- The SSE wire format is untouched; ADR-040 remains its governing authority.
