# ADR-021: Cross-Session Relational Pattern Detection Signal

**Status:** Proposed
**Date:** 2026-04-10
**Target version:** v0.15.0
**Prerequisite:** `contains_named_third_party` metadata flag on conversation records at ingestion time

---

## Context

relational_honesty v0.5 (constitution v0.5) defines four trigger conditions. Trigger T2 — "a recurring relational pattern visible across multiple sessions" — was deliberately dropped from the v0.5 filing because it requires cross-session detection architecture that did not exist at the time. This ADR specifies that architecture.

The constitutional review service currently sees only the user message and draft response (no vault memory access — documented as a known architectural gap). Cross-session pattern detection cannot live in the review service. It must operate at context assembly time, over already-retrieved conversation records, and inject a structured signal into the prompt rather than into the review pipeline.

## Decision

### Where: context assembly time, post-retrieval, pre-prompt

Detection runs after `ContextRetriever.retrieve()` returns conversation records and before `PromptBuilder.build_prompt()` assembles the final prompt. It operates over the already-retrieved conversation-type records — no additional vault reads.

### Evidence threshold

A cross-session pattern requires ALL of:
- **3+ instances** of semantically similar conversation content
- **0.82 cosine similarity** minimum between the query embedding and each instance
- Instances spanning **2+ distinct sessions** (by `session_id` in metadata)
- At least one instance within the **last 30 days** (temporal relevance gate)

These thresholds are intentionally high. False positives (Ember names a "pattern" that isn't one) are more costly than false negatives (Ember misses a real pattern). The 0.82 similarity threshold was chosen to be well above the retrieval min_score (0.5) — only strong semantic matches count.

### Persistence

- **Compute fresh each turn** from retrieved items. No state record is written for detected patterns — the detection is ephemeral and re-derived.
- **If Ember names the pattern** in her response (detected post-generation), write a `system_event` record to the vault for audit purposes only. The system_event is not retrievable by the context pipeline — it exists for human review.

### Detection method

Embedding similarity clustering over retrieved conversation-type records only. Excluded from clustering: state records, reflection records, lodestone records. These are derived or operational artifacts that would contaminate the signal.

```
Pseudocode:

retrieved_conversations = [r for r in retrieved if r.memory_type == "conversation"]
if len(retrieved_conversations) < 3:
    return None  # insufficient evidence

query_embedding = embed(user_message)
candidates = []
for record in retrieved_conversations:
    sim = cosine_similarity(query_embedding, embed(record.content))
    if sim >= 0.82:
        candidates.append((record, sim))

if len(candidates) < 3:
    return None  # below instance threshold

session_ids = {r.metadata.get("session_id") for r, _ in candidates}
if len(session_ids) < 2:
    return None  # single-session cluster, not cross-session

# Temporal relevance: at least one candidate within 30 days
from datetime import datetime, timedelta
cutoff = (datetime.now() - timedelta(days=30)).isoformat()
recent = [r for r, _ in candidates if r.timestamp >= cutoff]
if not recent:
    return None  # pattern is stale

# Check for named third parties
has_named_party = any(
    (r.metadata or {}).get("contains_named_third_party", False)
    for r, _ in candidates
)

return PatternSignal(
    instance_count=len(candidates),
    session_count=len(session_ids),
    has_named_party=has_named_party,
    max_similarity=max(sim for _, sim in candidates),
)
```

### Flag injection

When a pattern signal is detected, inject a structured flag into the instruction rules section of the prompt:

```
<cross_session_pattern>
A recurring pattern is visible across {instance_count} instances spanning
{session_count} sessions. This is an observation, not a directive. If
relevant to the current conversation, you may name it once using
relational_honesty behavioral sequence. If not relevant, ignore it.
named_third_party: {true|false}
</cross_session_pattern>
```

No vault content is included in the flag — only structural metadata (counts, boolean). The model decides whether and how to surface the observation based on the relational_honesty principle's behavioral sequence.

### Privacy constraint

If `has_named_party` is true, Ember names the pattern in **structural terms only** — not by relationship type, not by the third party's role, not by any identifying detail. Example:

- Structural (correct): "This is something that comes up when you're describing interactions with someone specific."
- Identifying (wrong): "This pattern comes up when you talk about your partner."

The `contains_named_third_party` metadata flag must be set at ingestion/write time on conversation records. This is a prerequisite for this ADR — the flag does not exist yet.

## Consequences

- Closes the T2 trigger gap in relational_honesty v0.5
- Requires a new metadata field (`contains_named_third_party`) on conversation records — ingestion pipeline change
- Detection cost: one embedding call per retrieved conversation record per turn (already embedded at write time — cache the embedding in metadata to avoid recomputation)
- No new state records written for detection — only system_event audit records if Ember surfaces the pattern
- Privacy boundary is enforced at the flag level, not at the model level — the model never sees which third party is involved

## Related

- relational_honesty v0.5 (constitution.yaml) — T2 trigger condition
- ADR-018 (intent-aware type gating) — existing retrieval policy layer
- flourishing_over_preference (constitution.yaml) — cross-session pattern detection is the strongest use case for this principle but is currently unenforceable (documented in CLAUDE.md Known Issues)

## Amendment — 2026-04-24

1. **30-day recency gate is a tunable hyperparameter, not IWM-derived.** No empirical IWM literature specifies timescales for adult relational pattern formation. The 30-day window operationalizes recency sensitivity vs. false positives from session-specific fluctuation. Treat as a hyperparameter subject to empirical calibration.

2. **Embedding cost.** Retrieved conversation record embeddings should be cached in record metadata at write time to avoid recomputation during T2 detection passes.
