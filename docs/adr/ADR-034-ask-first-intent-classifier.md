# ADR-034: Ask-First Intent Classifier — Three-Tier Hybrid Architecture

**Status:** Accepted
**Date:** 2026-04-20
**Version:** v0.17.0 (planned)

## Context

Ember's ask-first feature requires classifying every user message as either:
- **`needs_internet`** — requires current/external information that cannot come from the user's personal vault
- **`vault_answerable`** — a personal query that should be answered from the user's stored memories

This classification gate drives the ask-first behavioral contract:

1. Ember can use the internet (web search enabled)
2. Ember must ask before searching (`ask_first_mode=True`)
3. Ember may bypass ask-first when the user explicitly requests a search
4. Ember must cite sources when it searches

The current implementation is a keyword regex classifier in `src/context/policies.py`. It fails in both directions:

**False positives (over-triggering):** Personal queries containing volatile-sounding words — "I'm *currently* working on", "I've been watching a *show*", "what are my *latest* projects" — get classified as `needs_internet`. The classifier operates on word form without semantic role. "Currently" in "I'm currently working on X" has a completely different semantic position than "currently" in "what's currently in the news."

**False negatives (under-triggering):** Subtle external queries that don't match the keyword list slip through unclassified.

The root failure is modeling intent disambiguation as keyword matching. The fundamental question — "is the answer to this query located in the user's personal history, or in the external world?" — requires semantic context, not pattern matching.

## Decision

Replace the web-search intent classifier with a three-tier hybrid cascade:

```
User Query
    │
    ▼
[Stage 1: Structural Rules + Compound First-Person Guard]  ~2ms
    │ ambiguous
    ▼
[Stage 2: Embedding Similarity (all-MiniLM-L6-v2)]  ~15ms
    │ confidence < 0.65
    ▼
[Stage 3: qwen3:8b, non-thinking mode, JSON grammar]  ~300-500ms
```

### Stage 1 — Structural Rules with Compound Guard

Definite-internet signals (weather, stock price, today's headlines, live scores) route directly to `needs_internet` — but only when no compound first-person + no-external-anchor condition is present.

The first-person guard requires **both** conditions to block an internet signal:
1. First-person marker present (`my`, `I'm`, `I've`, `I said`, `I am`, `I was`, `I have`)
2. AND no external-world anchor word present (`weather`, `price`, `news`, `election`, `score`, `currently in the news`, etc.)

A single first-person marker alone is insufficient. "What's my doctor currently recommending for my condition?" is first-person but still vault-answerable. "I've been reading that inflation is rising — is that still true?" is first-person but needs external verification. The compound condition handles the real failure case: "I'm currently working on" (first-person + no external anchor → vault) vs. "what's currently in the news" (no first-person + implicit external anchor → internet).

Explicit search requests (`search the web`, `google`, `look it up online`) always bypass ask-first entirely — the user's own words are the confirmation (rule 3 of the behavioral contract).

### Stage 2 — Embedding Similarity

Use `semantic-router` (aurelio-labs) with `all-MiniLM-L6-v2` (local, no network call). Maintain a labeled example pool per class, bootstrapped from real Ember-2 user queries.

**Labeling strategy:** Start with 30 real examples per class (60 total). Ship. Collect production misclassifications from the logging pipeline (see below). Reach 80 examples per class organically. Do not use CLINC150 or NaturalQuestions as the primary bootstrap source — distribution mismatch. Those datasets do not look like Ember queries.

Confidence threshold: 0.65. Below this, escalate to Stage 3.

### Stage 3 — LLM Fallback

qwen3:8b with `think: false` (non-thinking mode is load-bearing — thinking mode adds 2-5 seconds). JSON grammar constraint forcing binary output. Hard timeout: 800ms (configurable via config, not hardcoded — benchmark on target hardware first).

**Timeout fallback: `vault_answerable`, not `needs_internet`.** A timeout on Stage 3 means the query is genuinely ambiguous. Defaulting to `needs_internet` on ambiguity would silently bypass the ask-first behavioral contract (rule 2). The correct conservative behavior is to treat ambiguous queries as vault-answerable, answer from memory, and let the user explicitly request a search if they want one.

### Output Space

The classifier technically has three states: definitely vault, definitely internet, or genuinely uncertain. The three-tier cascade expresses this naturally — Stage 1 catches high-confidence cases at either pole, Stage 2 handles the middle, Stage 3 forces a decision on the residual. The timeout fallback to `vault_answerable` is the system's abstention path.

### Logging (mandatory)

Every classification decision must be logged with:
- The normalized query (truncated to 200 chars)
- Which stage resolved it (`stage1`, `stage2`, `stage3`, `timeout`)
- The label assigned (`needs_internet`, `vault_answerable`)
- The confidence score (Stage 2 only)
- Whether the result was later confirmed or corrected (post-hoc feedback, if available)

Without this log, the 150-label SetFit upgrade path has no data pipeline. The log is not a debugging aid — it is the training data collection mechanism.

### Upgrade Path

Once 150+ real Ember-2 queries are labeled per class (from the log), train a SetFit model (Tunstall et al., 2022). SetFit at ~80MB runs at 10-15ms CPU latency with 92-97% accuracy on domain-similar data. It replaces Stage 1 + Stage 2 entirely. A `TODO: SetFit upgrade when 150 labels/class accumulated` comment must be placed in the classifier module.

## Rationale

- The cascade from cheap-fast to expensive-accurate is the correct pattern for local hardware where every millisecond is real latency
- The compound first-person guard addresses the documented failure mode (false positives on self-referential queries) without introducing the new failure mode (misclassifying first-person + external-anchor queries)
- Timeout defaulting to `vault_answerable` preserves the behavioral contract under load
- Starting with 30 labeled examples rather than 80 ships faster and grounds the label pool in real usage rather than proxy datasets
- The logging requirement converts production traffic into training data, making the system self-improving without requiring explicit labeling effort

## Consequences

+ Eliminates the documented false-positive pattern on personal queries
+ Preserves behavioral contract under Stage 3 timeout/load
+ Self-improving: production misclassifications feed back into the example pool
+ SetFit upgrade path is concrete and achievable

- Stage 2 requires an initial labeling session (30 examples/class minimum)
- Stage 3 adds up to 800ms latency on the ambiguous long tail — this is the accepted tradeoff for correctness
- `semantic-router` is a new dependency; evaluate for local-only operation (no telemetry to external services)

## Alternatives Considered

### Keep keyword classifier, patch false positives
Rejected. The failure mode is architectural — word-form matching without semantic context. Patching produces an arms race of edge cases. The current classifier already has 200+ lines of patterns and is still broken.

### LLM-only classifier (skip Stages 1+2)
Possible but adds 300-500ms to every single message. Unacceptable for vault-answerable queries, which represent the majority of Ember traffic.

### Binary classifier only (no cascade)
First Principles analysis suggests Stages 2+3 alone may outperform the full three-tier system with less complexity. This is a valid future simplification — once production data validates Stage 2 accuracy, Stage 1 may be removable. Keeping Stage 1 for v0.17.0 because it is zero-dependency and provides an immediate fix for the most egregious false positives.

## Relationship to Other ADRs

- ADR-018 (intent-aware type gating) — ADR-018 gates which memory types are eligible for retrieval based on query intent. This ADR determines whether the query routes to web search vs. vault retrieval. Upstream of ADR-018; both are required.
- ADR-004 (ingestion pipeline) — unaffected.
- ADR-033 (ChatGPT import role separation) — unaffected.

## References

- Tunstall et al. "Efficient Few-Shot Learning Without Prompts (SetFit)." arXiv:2209.11055, 2022.
- Ong et al. "RouteLLM: Learning to Route LLMs with Preference Data." arXiv:2406.18665, 2024.
- "RAGRouter." arXiv:2505.23052, 2025.
- "REIC: Retrieval-Augmented Intent Classification." arXiv:2506.00210, 2025.
- aurelio-labs/semantic-router — https://github.com/aurelio-labs/semantic-router
- CLINC150 dataset (Larson et al., 2019) — personal assistant intents, secondary bootstrap source only
