# ADR-030: Post-Generation Coaching Filter Architecture

**Status:** Accepted (final)
**Date:** 2026-04-13

## Context

Manual testing identified a persistent failure mode: coaching-frame closings and identity collapse strings in generated responses. These are patterns where the model slips into a generic assistant/coach persona ("Remember, you deserve...", "I'm here for you whenever...") rather than maintaining Ember's voice. The constitutional review layer (ADR-001) catches safety violations but does not target stylistic persona drift. The grounding verification layer (ADR-019) catches factual fabrication but not tonal failure.

A dedicated filter is needed that detects and rewrites these patterns without disrupting the existing post-generation pipeline.

## Decision

Implement a two-stage post-generation filter in `src/llm/coaching_filter.py`.

### Stage 1: Pattern Matcher

- Regex/string matching for coaching-frame closings and identity collapse strings.
- Fires on emotional/relational intent only — factual and retrieval responses bypass Stage 1.
- Detections result in either deletion (when the pattern can be cleanly removed) or escalation to Stage 2.

### Stage 2: Small Model Rewrite

- Fires only when Stage 1 detects a pattern requiring natural language rewriting rather than simple deletion.
- Uses a small, fast model call to rewrite the flagged segment while preserving meaning.
- Does not fire on clean deletions.

### Pipeline Placement

Post-generation, pre-stream. Runs after the model generates a response and before the response is streamed to the user. Runs after grounding verification (ADR-019), before final delivery.

### Logging

All interventions are logged with:
- Intent class (emotional, relational, etc.)
- Pattern matched
- Original segment
- Rewritten segment (if Stage 2 fired)
- Stage fired (1 or 2)

### Judge Call Separation

Flag detection and dimensional scoring use separate judge calls to prevent interference. A single combined call risks the scoring context biasing the flag detection or vice versa.

## Rationale

- Two-stage design avoids unnecessary model calls — most coaching patterns can be deleted without rewriting.
- Limiting Stage 1 to emotional/relational intent prevents false positives on factual responses that happen to contain similar phrasing.
- Post-generation placement preserves the full generation pipeline and enables logging of the unfiltered draft.
- Separate judge calls for detection vs scoring ensure neither task biases the other.

## Consequences

+ Coaching-frame closings are caught systematically rather than relying on constitutional rules.
+ Logging provides full audit trail for filter tuning.
+ Stage 2 fires rarely — minimal latency impact in the common case.
- Adds another post-generation step (after constitutional review and grounding check).
- Pattern list requires ongoing maintenance as new failure modes are discovered.
- Stage 2 model call adds latency when it fires.

## Alternatives Considered

### Extending constitutional review to cover coaching patterns
Rejected: constitutional review evaluates policy compliance, not stylistic drift. Mixing concerns reduces precision of both systems.

### Pre-generation prompt engineering only
Rejected: prompt instructions reduce but do not eliminate coaching patterns. Post-generation filtering is the documented effective approach for persistent persona drift.

### Single-stage filter (pattern match + delete only)
Rejected: some coaching patterns are embedded mid-sentence and cannot be cleanly deleted without breaking the response. Stage 2 handles these cases.