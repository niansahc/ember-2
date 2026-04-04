# ADR-018: Intent-Aware Memory Type Gating

**Status:** Accepted
**Date:** 2026-04-03
**Version:** v0.13.0

## Context

Ember's retrieval pipeline (ADR-005) currently operates in two stages:

1. Candidate gathering -- semantic similarity search across all eligible memory types
2. Ranking -- policy-weighted scoring, recency, project boost, dedup

The problem: stage 1 has no awareness of query intent when determining which memory types are eligible candidates. Every query draws from the same pool. A work-task query may surface health records, emotional journal entries, or relationship state. A therapy-adjacent conversation may surface professional project records. The model then receives this mixed context and must make sense of it.

This is a retrieval policy problem, not a model problem. The fix belongs in retrieval_policy.py, not in prompting or constitutional review.

Three independent research sources converge on this conclusion:

CIMemories (Mireshghallah et al., ICLR 2026; arxiv:2511.14937): Frontier models show up to 69% attribute-level violations when drawing on persistent memory in inappropriate contexts. The same attribute may be appropriate to surface in one query context and inappropriate in another. Ember's architecture is well-positioned because retrieval policy is explicit code, not model judgment. But the current policy only weights types; it does not gate them.

MemX (low-confidence rejection): MemX applies a consistent min_score floor across all retrieval paths and returns empty rather than weak context. When retrieval returns nothing meaningful, the prompt builder should signal "no relevant memory found" explicitly rather than injecting noise. This directly addresses the documented qwen3:8b hallucination pattern.

Contextual Integrity as retrieval policy: Nissenbaum's CI framework defines privacy as appropriate information flow -- governed not just by what flows but under what conditions it is appropriate to share. A work/task query context has different appropriate memory types than a personal/emotional query context. The retrieval policy should encode these norms.

## Current State

classify_query() in src/context/policies.py returns a ContextPolicy with memory_weight, reflection_weight, recency_bias, and boolean hints. What is missing: eligible_memory_types -- which memory types should be candidates for this query context. Currently all types are always eligible.

## Decision

Add eligible_memory_types, suppress_memory_types, and min_score fields to ContextPolicy. These gate candidate eligibility before ranking, not after.

### Schema Addition
```python
@dataclass
class ContextPolicy:
    name: str
    memory_weight: float = 1.0
    reflection_weight: float = 1.0
    recency_bias: float = 0.0
    diversity: bool = False
    prefer_experiences: bool = False
    prefer_active_work: bool = False
    prefer_exact_matches: bool = False
    state_boost: float = 0.0
    use_web_search: bool = False
    eligible_memory_types: list[str] | None = None  # None = all types eligible
    suppress_memory_types: list[str] = field(default_factory=list)
    min_score: float = 0.25
```

eligible_memory_types = None means no change -- all types eligible. Full backward compatibility.

### Intent-to-Type Mapping

work/task queries (prefer_active_work=True, activity/recent_activity policies):
- suppress: journal entries tagged with health, grief, relationship, emotional
- suppress: personal state categories (mood, energy) unless explicitly relevant
- rationale: professional context should not surface sensitive personal records

reflective/personal queries (reflective policy):
- eligible: all types, no suppression
- rationale: reflective queries explicitly invite cross-domain synthesis

status/state queries:
- eligible: state, task, project, profile
- suppress: ingested reference material, old reflections
- rationale: "what am I working on" should surface operational context

factual recall queries:
- eligible: ingested, reference, profile, conversation
- rationale: factual queries want information records

default:
- eligible: all types, min_score: 0.25

### Min Score Floor

Apply consistent min_score floor across all retrieval paths. Records below min_score excluded before ranking. Default: 0.25.

When filtered candidate pool is empty, prompt builder receives empty memory section and renders: "No relevant memory found for this query." Model responds from its own knowledge rather than weak context.

### Implementation Location

All changes in src/context/policies.py (policy definitions) and src/context/retriever.py (candidate filtering). Ranker, service, and prompt builder are unchanged except for the empty context message.

Filtering at retriever level before ranking:
```python
def _apply_type_gate(self, items, policy):
    if policy.suppress_memory_types:
        items = [i for i in items if i.memory_type not in policy.suppress_memory_types]
    if policy.eligible_memory_types is not None:
        items = [i for i in items if i.memory_type in policy.eligible_memory_types]
    items = [i for i in items if i.score >= policy.min_score]
    return items
```

## Rationale

- Retrieval policy is explicit code -- Ember's primary defense against CI violations
- Type gating belongs upstream of ranking -- eligibility before scoring
- eligible_memory_types = None default preserves full backward compatibility
- min_score floor addresses qwen3:8b hallucination by eliminating weak context injection
- Empty context is better than noisy context
- Changes isolated to two files; ranker, service, prompt builder untouched

## Consequences

+ Context packets more coherent -- work queries don't surface sensitive personal records
+ qwen3:8b hallucination pattern directly addressed
+ Retrieval policy more inspectable
+ Backward compatible
+ Foundation for future CI-aware policy expansion

- Type gating requires judgment -- miscategorization silently excludes relevant records
- Suppression rules need empirical validation -- run retrieval eval before and after
- Empty context path needs prompt builder update

## Open Questions

- Should suppression rules be configurable in .env or config/ rather than hardcoded? For v0.13.0 hardcode with clear comments. Make configurable in a future version.
- The explicit "no relevant memory found" signal in the prompt builder is required, not optional. When the filtered candidate pool is empty after type gating and min_score floor, the prompt builder must render an explicit absence message rather than silently passing empty context. The model should be instructed to acknowledge uncertainty rather than generate from parametric memory. This is the third step of the compound intervention for the qwen3:8b hallucination pattern: min_score floor (eliminates weak candidates) + empty pool detection (catches empty pool before prompt assembly) + explicit absence signal (tells model to acknowledge uncertainty rather than confabulate).
- Should eval harness include type gating correctness test cases? Yes -- add at least two cases per suppressed type.
- How does type gating interact with nature block injection? It doesn't -- nature is injected unconditionally outside the retrieval pipeline.

## Relationship to Other ADRs

- ADR-005 (context ranking) -- this ADR adds a pre-ranking eligibility gate. ADR-005 covers ranking after candidates are gathered. Distinct stages; ADR-005 unchanged.
- ADR-007 (project-scoped retrieval) -- project boost applied after type gating; unchanged.
- ADR-016 (nature layer) -- nature block injected outside retrieval pipeline; unaffected.

## References

- Mireshghallah, N. et al. "CIMemories." ICLR 2026. https://arxiv.org/abs/2511.14937
- MemX (Sun et al., arXiv 2603.16171, March 2026) -- low-confidence rejection; stability-over-recall
- Nissenbaum, H. (2004) -- Contextual Integrity framework; Washington Law Review
- ADR-005: Multi-Stage Context Retrieval and Ranking Strategy
- ADR-007: Project-Scoped Retrieval

---

## Amendment — 2026-04-04

### Change: "No Memory Found" Signal

The original signal ("Answer from your own knowledge and acknowledge if you are uncertain") assumes the model knows when it is uncertain. A model filling retrieval gaps with parametric fabrication does not experience uncertainty -- it experiences confidence. The soft instruction does not reach the failure.

Revised signal: Honest, specific, and testable. Does not prohibit confabulation (that is ADR-019's job). Gives the model a concrete fallback phrase rather than an abstract instruction.

### Deferred: Sensitive Type min_score Floor

Research suggested raising the min_score floor for sensitive memory types from 0.25 to 0.5. No specific sensitive type retrieval failure was observed in manual testing. Implementing without an observed failure is coding to the test, not solving a bug. Deferred until a specific failure is observed in practice.

### Relationship to ADR-019

ADR-018 is the pre-generation gate: prevent weak context from entering the model.
ADR-019 is the post-generation check: catch fabrications that occur despite good retrieval.
Both are needed. Neither substitutes for the other.
