# ADR-041: PreGeneration Terminal Router and the Enrichment-Independence Contract

**Status:** Accepted
**Date:** 2026-06-15
**Target:** v0.18.1
**Related:** ADR-040 (SSE wire contract), issue #93 (decompose `chat_completions`)

## Context

`chat_completions` in `src/api/openai_adapter.py` had grown to ~1350 lines. Issue
#93 decomposes it in three sequenced PRs:

- **(a)** consolidate the SSE serializer + freeze the wire contract (ADR-040) -- shipped.
- **(b)** extract a terminal pre-generation router -- this ADR.
- **(c)** extract a `GenerationContext` + request-enrichment pipeline -- later.

The function opened with a run of short-circuits that return a canned reply and
skip generation entirely: empty-message, override (jailbreak), onboarding, and a
bare-web-marker clarification. PR (a) had already routed all of them through the
single `early_return_response` helper, which owns the stream-vs-JSON decision
(the A1 invariant, CLAUDE.md Bug Standard #1). They were still four scattered
`return` sites interleaved with request-mutating enrichment steps.

## Decision

Extract a `PreGenerationRouter`: an ordered chain of **terminal interceptors**
that run as one step and produce, at most, one terminal reply funneled through a
**single** `early_return_response` call.

### The contract: enrichment-independent terminal interceptor

An interceptor is a callable `(RouterContext) -> TerminalReply | None`. It must:

1. read **only** the `RouterContext`;
2. **not** mutate the context or any value the enrichment pipeline or the
   generation handler later reads;
3. confine any side effects to a service that needs **no enrichment-resolved
   inputs** (no `session_id`, `project_id`, vault flags, resolved context, etc.).

We deliberately did **not** call this "side-effect-free." That term is false of
the code: onboarding writes profile and state records. The load-bearing property
is enrichment-independence -- whether an interceptor can run before the
enrichment pipeline using only the message-level inputs available at the top of
the request.

### Scope: three interceptors, clarification deferred

In PR (b) the router holds exactly three interceptors, in this precedence order
(mirroring the historical top-to-bottom order):

1. **empty** -- blank message and no image parts. Pure.
2. **override** -- `_is_override_attempt` jailbreak heuristic. Pure.
3. **onboarding** -- `onboarding_service.is_active()` / `.handle()`. Side
   effects are fully encapsulated in `OnboardingService` and need only the
   message, so it satisfies the contract.

The **clarification** short-circuit is **not** extracted. It writes two
adapter-level conversation turns keyed by `session_id`/`project_id` and gated on
`is_test`/`vault_enabled` -- all enrichment-resolved. It is therefore not
enrichment-independent and stays inline until PR (c), where `GenerationContext`
can carry those values cleanly. It still flows through `early_return_response`,
so the A1 invariant holds for it regardless.

### Deviation from issue #93's stated signature

Issue #93 described interceptors as `(ctx) -> Optional[Response]`. We instead
return `TerminalReply` **data** (`text`, `label`) and let the single caller
translate it into a response via `early_return_response`. Rationale:

- **Stronger A1 guarantee.** The whole point is one funnel for the stream-vs-JSON
  decision. Returning data gives exactly **one** `early_return_response` call
  site instead of three -- a single place that can ever pick the response type.
- **No import cycle.** Interceptors that returned `Response` would need the
  response builders in `openai_adapter`, while `openai_adapter` imports the
  router -- a cycle. Returning data keeps the generic module dependency-free.
- **Simpler tests.** Routing decisions are asserted on `TerminalReply` data; the
  stream-vs-JSON behavior is tested once at the funnel (and in the A1 regression).

### Module split: mechanism vs. interceptors

- `src/api/pregeneration.py` (new) holds the **generic mechanism**:
  `RouterContext`, `TerminalReply`, and `PreGenerationRouter`. It carries no
  domain knowledge and imports nothing from `openai_adapter`, so no import cycle
  can form.
- The **interceptor functions** stay in `openai_adapter`, beside their
  dependencies (`_is_override_attempt`, `onboarding_service`). No symbols move,
  so the ~10 test files that patch those names at their `openai_adapter`
  location keep working unchanged.

`RouterContext` and `TerminalReply` are frozen dataclasses, enforcing the
"must not mutate the context" half of the contract structurally.

`RouterContext` is deliberately minimal -- `latest_user_message`, `stream`,
`image_parts`, `completion_id` -- not the full `GenerationContext` (PR c). The
`completion_id` is minted once per request so every terminal reply and its
early-return log line share one id.

### Ordering note

The empty and override checks now run **after** the image-placeholder
normalization (previously before it). This is behavior-preserving: an image-only
upload is non-empty under the empty check either way (placeholder fills the text
and `image_parts` is truthy regardless), and the placeholder substitutes a fixed
internal string that is never an override pattern. Onboarding stays after the
placeholder exactly as before, so it sees the same message it always did.

## Consequences

- All terminal early-return paths for empty/override/onboarding are built in one
  place; the A1 stream-vs-JSON invariant has a single enforcement point.
- `chat_completions` shrinks: its terminal-routing job becomes normalize -> build
  `RouterContext` -> run router -> one `early_return_response` call.
- PR (c) inherits a clean seam: `RouterContext` is the precursor to
  `GenerationContext`, and clarification migrates into the router once its
  dependencies are carried by the enrichment context.
- The lockstep change procedure for the SSE wire format remains governed by
  ADR-040; this ADR does not alter any emitted frame.
