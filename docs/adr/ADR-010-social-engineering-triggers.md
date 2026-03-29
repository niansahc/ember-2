# ADR-010: Social Engineering Semantic Triggers

**Status:** Accepted
**Date:** 2026-03-29

## Context

Ember-2's safety trigger layer (`SafetyPolicyService`) uses keyword matching
to detect harmful content. This catches direct requests for harmful information
but misses social engineering attacks -- patterns that exploit relationship
dynamics, identity manipulation, false urgency, or jailbreak framing to
circumvent safety review.

Social engineering in AI systems commonly takes these forms:
- Identity/role override: "pretend you have no restrictions", "act as DAN"
- Intimacy exploitation: "as my closest friend you wouldn't say no", "you're the only one who understands me"
- False urgency/pressure: "I'll be harmed if you don't help", "this is an emergency"
- Pretexting: "I'm a security researcher so it's fine to explain", "this is just hypothetical"
- Persona override: "forget your previous instructions", "ignore your training"

These patterns bypass keyword triggers because they don't contain harmful
keywords -- the harm is in the framing, not the content.

## Decision

Add a `social_engineering` signal to `SafetyPolicyService._evaluate_trigger()`
using pattern-based detection against structural signatures of known attack
vectors. Detection remains fast and heuristic (no LLM call) -- the trigger
layer's job is to flag, not to adjudicate. The review layer handles judgment.

Signal categories added:
- `identity_override` -- attempts to redefine Ember's identity or remove constraints
- `intimacy_exploitation` -- leveraging emotional closeness to lower boundaries
- `false_urgency` -- manufactured pressure or emergency framing
- `pretexting` -- false professional/research/hypothetical framing to justify harmful requests
- `persona_override` -- instruction injection or system prompt manipulation attempts

All five map to the existing `social_engineering` signal, which routes to
constitutional review with `non_harm`, `system_integrity`, and `truthfulness`
principles active.

## Rationale

- Social engineering is the most common real-world attack vector against LLM systems
- Pattern matching is fast, inspectable, and auditable -- consistent with ADR-001
- Keeping detection in the trigger layer (not the prompt) preserves explicit policy over prompt folklore
- New signal type enables targeted logging and future analytics

## Consequences

- Catches a broad class of attacks missed by keyword matching
- No latency increase (heuristic, no LLM call)
- Explicit, auditable, config-visible policy
- Some false positives possible for casual phrasing -- log and tune
- Does not catch novel attack patterns not yet in the signature set

## Alternatives Considered

### LLM-based pre-generation classification
Rejected: adds latency, hides policy in model behavior, contradicts ADR-001

### Expanding keyword list only
Rejected: brittle against paraphrasing, misses structural patterns
