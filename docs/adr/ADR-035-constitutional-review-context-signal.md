# ADR-035: Constitutional Review Context Signal

**Status:** Proposed
**Date:** 2026-04-24
**Target version:** v0.17.0

## Context

`ResponseReviewService` currently receives only `user_message` and `draft_response` at review time. No vault memory, no context packet, no conversation history. This is documented in `docs/KNOWN_ISSUES.md` as "Constitutional review service context blindness."

Two downstream capabilities are blocked by this gap:

- **relational_honesty T2** (ADR-021) — cross-session pattern detection cannot surface an observation at review time because the reviewer has no way to see that a pattern was detected upstream.
- **flourishing_over_preference cross-session observation** — the principle's strongest use case (naming a stated-value/behavior conflict visible across multiple sessions) is currently unenforceable for the same reason.

Giving the review service open vault access would violate the separation between retrieval logic and review logic (a core architectural rule — see CLAUDE.md "What Not to Touch"). A narrow, structured context signal is the minimum change that unblocks both capabilities while preserving that separation.

## Decision

Pass a narrow context signal into `SafetyReviewContext` — not open vault access. The signal contains structural metadata only; no raw vault records and no semantic content from retrieved memory.

### Signal contents

1. **`is_vault_grounded: bool`** — whether the current turn was built on retrieved vault items (i.e. the context packet contained non-empty `memory_items`, `state_items`, or `reflection_items`). Lets the reviewer distinguish a hallucinated claim from one that has retrieval support.
2. **`t2_pattern_category: str | null`** — if cross-session pattern detection (ADR-021) fires, the taxonomy category label only (e.g. `"relational"`, `"directional"`). No counts, no semantic content, no record text. Null when no pattern signal is present.

No raw vault records, no conversation history, no embedding data, no record IDs. The category label is intentionally opaque — it tells the reviewer *that* a pattern is active and *which family* it belongs to, without anchoring the LLM to a specific claim to confirm.

Rationale for category-only design: clinical LLM anchoring research shows that irrelevant or minimal hints do not anchor reviewer LLMs (the anchoring effect scales with specificity of the hint). A category label provides enough signal to activate the relevant principle without supplying content the reviewer might unconsciously ratify.

### Two-step review prompt for T2-triggered cases

When `t2_pattern_category` is non-null, the review prompt becomes a two-step interaction:

1. **Observation step.** Ask the reviewer to first describe what it observes in the draft relevant to the triggered principle — no verdict yet.
2. **Verdict step.** Then, conditioned on the observation, render the allow / revise / refuse-redirect decision.

Separation of observation from verdict is empirically grounded in clinical LLM anchoring research: two-step prompting reduces anchoring errors versus single-pass "did the draft handle principle X correctly?" prompting. The observation step forces the reviewer to commit to a description of the draft before being asked to judge it, which reduces the tendency to retro-fit reasoning to an implicit verdict.

Non-T2 reviews remain single-pass — the two-step structure is only applied when a T2 signal is present, to contain the latency cost to the cases that actually benefit.

### What this is *not*

- Not vault access. The review service still cannot read memory records.
- Not a history channel. No prior turns, no previous drafts, no previous verdicts are passed.
- Not a retrieval pipeline. The signal is computed upstream (at context assembly time for `is_vault_grounded`, at T2 detection time for `t2_pattern_category`) and handed to review as a structured value.
- Not unbounded. Future additions to `SafetyReviewContext` require a new ADR amendment — the signal fields are an explicit allowlist.

## Consequences

- Closes the prerequisite for relational_honesty T2 (ADR-021) to produce a verdict-affecting review outcome.
- Closes the prerequisite for `flourishing_over_preference` cross-session observation to be enforceable.
- Review service remains effectively stateless — no vault reads at review time. The upstream detection and retrieval services remain the single source of truth for vault access.
- Anchoring risk is minimized by the category-only design of `t2_pattern_category`. The reviewer sees the family of pattern, not the content.
- Two-step prompt is only applied when a T2 signal is present — single-pass review remains the common path and absorbs no additional latency on non-T2 turns.
- `KNOWN_ISSUES.md` entries for "Constitutional review service context blindness" and "flourishing_over_preference v0.2 cross-session gap" become targetable in v0.17.0 (see updated entries).

## References

- ADR-021 (cross-session relational pattern detection) — T2 spec; produces the pattern signal this ADR consumes.
- ADR-010 (social-engineering triggers) — prior art for trigger-to-review handoff patterns in the safety layer.
- Constitution v0.7 — `flourishing_over_preference` cross-session use case, `relational_honesty` T2 condition.
- Clinical LLM anchoring research — two-step prompting reduces anchoring errors vs. single-pass verdict prompts; irrelevant hints do not anchor reviewer LLMs.