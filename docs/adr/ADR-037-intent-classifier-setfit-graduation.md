# ADR-037: Intent Classifier SetFit Graduation

**Status:** Proposed
**Date:** 2026-08-31
**Target:** v0.19.0

## Context

ADR-034 (ask-first intent classifier) established the three-tier cascade for classifying every user query as `needs_internet` or `vault_answerable`: Stage 1 structural rules, Stage 2 embedding similarity against a labeled example pool, Stage 3 qwen3:8b fallback with a timeout that defaults to `vault_answerable`. ADR-034's own "Upgrade Path" section already specifies the next step: once 150+ real Ember-2 queries are labeled per class (collected via the mandatory classification log), train a SetFit model (Tunstall et al., 2022) to replace Stage 1 + Stage 2 entirely. `src/llm/intent_classifier.py:20` carries a literal `TODO: SetFit upgrade when 150 labels/class accumulated from logs.` comment placed per that mandate.

`docs/Ember2_TDD.md` tracks this as a blocked v0.19.0 task: the SetFit labeling session (150 examples/class, 0.85 confidence threshold for the Stage 1/Stage 2 cascade) is listed as a tracked v0.19.0 item, explicitly gated on this ADR existing (`Ember2_TDD.md:1788`, `2102-2104`). The threshold evidence — W5H2 + SetFit (arXiv:2602.18922): 91.1% accuracy at 22M parameters, 2-5ms latency versus 68.8% zero-shot for a 20B LLM — is held in the TDD's research log pending this ADR's rationale section (`Ember2_TDD.md:2100-2104`).

A partial implementation ("Step A": 7 new labeled-example buckets — `status_state`, `reflective`, `factual_recall`, `recent_activity`, `recent`, `activity`, `is_identity` — added to `src/llm/classifier_examples.py`) already exists, but only on branch `feat/adr-037-step-a-classifier-examples` (commit `d3c1142`, 2026-04-28). That branch diverged from `main` at `efcea2e` (2026-04-25) and has never been rebased or merged — `main` is now three-plus months and dozens of merges ahead. The branch's own code carries the comment `# Step B; Stage 2 imports EXAMPLES, not MULTICLASS_EXAMPLES.` — the new `MULTICLASS_EXAMPLES` export is data with no consumer. No document (TDD, ADR-034, commit messages) fully enumerates Steps B through E; only Step A's own commit message gestures at what B, C, and D might involve, and Step E is unnamed anywhere in the project's history.

`README.md`'s v0.18.0 section currently states that "ADR-037 classifier migration" shipped in that release. That claim is inaccurate — none of this work is on `main` — and is a documentation correction out of scope for this ADR.

## Decision

Graduate ADR-034's Stage 1 + Stage 2 to a trained SetFit model once 150+ labeled examples per class exist, exactly as ADR-034's own Upgrade Path section specifies. **Stage 3 is explicitly untouched by this ADR** — the qwen3:8b LLM fallback, its JSON grammar constraint, and the timeout-defaults-to-`vault_answerable` behavioral contract remain as ADR-034 defined them. This ADR governs only the SetFit replacement of Stages 1 and 2.

## Scope

**In scope:** `src/llm/intent_classifier.py`, `src/llm/classifier_examples.py`.

**Out of scope:** `src/context/policies.py`'s `classify_query` and its `RELATIONAL_KINSHIP_NOUNS` / `RELATIONAL_IDENTITY_DOMAINS` pattern matching. That logic filters which memory types are eligible for retrieval on relational/identity queries — a distinct concern from the needs_internet/vault_answerable ask-first gate this ADR governs. `classify_query` already calls into the ADR-034 cascade via `classify_intent()`; that integration is unaffected by this ADR.

Step A's `is_identity` bucket was designed, per its own commit message, to eventually absorb pet-possessive and kinship identity routing currently living in `policies.py`, migrating that responsibility into the intent classifier's label space. That migration is **descoped from this ADR**. It would fold a retrieval-filtering concern into the ask-first intent gate, which this ADR does not adopt. If kinship/identity routing consolidation is still wanted, it belongs in a separate future ticket against `policies.py`, not this graduation.

## Steps

| Step | Scope | Status |
|---|---|---|
| A | Labeled example buckets in `classifier_examples.py` | Exists but stale and unmerged. Branch `feat/adr-037-step-a-classifier-examples`, commit `d3c1142`, diverged from `main` at `efcea2e` (2026-04-25). Must be rebased onto current `main` and merged before Step B can start. |
| B | Wire `MULTICLASS_EXAMPLES` into Stage 2 classification | Not started. |
| C/D | Delete `RELATIONAL_KINSHIP_NOUNS` / identity keyword bags from `policies.py` (per Step A's commit-message intent) | **Descoped from this ADR** — see Scope section. File as a separate future ticket against `policies.py` if still desired. |
| E | Undefined | No historical record — TDD, commit messages, or ADR-034 — names what Step E was intended to cover. Marked TBD. A future amendment to this ADR should define it if and when scope is identified. |

## Rationale

- ADR-034 already committed to this upgrade path; this ADR formalizes the trigger (150 labels/class) and threshold (0.85 confidence) with citation-backed evidence rather than leaving them as an unwritten TODO.
- W5H2 + SetFit (arXiv:2602.18922) demonstrates SetFit at 22M parameters reaches 91.1% accuracy at 2-5ms latency, a 700x latency advantage over a 20B-parameter LLM zero-shot baseline — consistent with ADR-034's stated rationale for a cascade design (cheap-fast to expensive-accurate) and directly supports replacing Stages 1+2, which exist for exactly the latency reasons SetFit addresses at higher accuracy.
- Keeping scope confined to `intent_classifier.py` and `classifier_examples.py` keeps this ADR independent of the unrelated `classify_query` kinship-routing concern, avoiding the scope bleed Step A's own design implied.
- Marking Steps C/D/E as descoped or undefined, rather than inventing a scope for them now, keeps this ADR's claims honest against what can actually be verified in the codebase and project history.

## Consequences

+ Closes the SetFit upgrade path ADR-034 already promised, unblocking the v0.19.0 SetFit labeling session tracked in the TDD.
+ Formalizes the 0.85 confidence threshold and 150-label/class trigger with citation-backed evidence.
+ Scope stays independent of the unrelated `classify_query` / kinship-routing concern in `policies.py`.

- Step A requires a rebase onto current `main` before any further work can proceed — this is real, unstarted effort; the branch is not close to mergeable as-is.
- Steps C, D, and E remain open questions this ADR deliberately does not resolve. A reader expecting a complete A-E roadmap will not find one here.
- Stage 3's LLM fallback remains a qwen3:8b round-trip on the ambiguous long tail; this ADR does nothing to reduce that cost, since Stage 3 is out of scope.

## Alternatives Considered

### Fold Steps C/D (kinship keyword-bag removal) into this ADR
Rejected. `RELATIONAL_KINSHIP_NOUNS`/`RELATIONAL_IDENTITY_DOMAINS` govern memory-type eligibility for retrieval, not the needs_internet/vault_answerable gate. Merging them into the SetFit graduation would make this ADR's "Decision" section describe two unrelated architectural changes, and would make the intent classifier's label space responsible for a retrieval-filtering behavior it was never designed to own.

### Invent a defined scope for Step E
Rejected. No source — TDD, ADR-034, commit messages, prior PRs — names what Step E was meant to cover. Fabricating scope for it here would misrepresent project history rather than document it.

## Relationship to Other ADRs

- **ADR-034** (ask-first intent classifier) — parent. This ADR executes the Upgrade Path section ADR-034 itself specifies; same upstream/prerequisite citation pattern ADR-034 uses to relate itself to ADR-018.
- **ADR-018** (intent-aware type gating) — unaffected. Distinct concern (memory-type eligibility, not the ask-first gate).

## References

- `docs/adr/ADR-034-ask-first-intent-classifier.md` — Upgrade Path section (SetFit trigger and replacement scope).
- Tunstall et al. "Efficient Few-Shot Learning Without Prompts (SetFit)." arXiv:2209.11055, 2022.
- W5H2 + SetFit intent classification at agent scale. arXiv:2602.18922, February 2026.
- `docs/Ember2_TDD.md` §25.3 (v0.19.0 research graduation — SetFit labeling session task).
- Commit `d3c1142`, branch `feat/adr-037-step-a-classifier-examples` (Step A implementation, stale/unmerged).
- `src/llm/intent_classifier.py:20` — existing TODO marker this ADR resolves.
