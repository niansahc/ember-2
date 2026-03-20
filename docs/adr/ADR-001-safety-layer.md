# ADR-001: Post-Generation Constitutional Safety Review

Status: Accepted
Date: 2026-03-19

## Context

Ember-2 requires a safety system that prevents harmful outputs while
preserving visibility into model reasoning and behavior.

Pre-generation filtering reduces transparency and makes debugging difficult.

## Decision

Safety will be implemented as a post-generation constitutional review step.

Flow:

user input
→ context retrieval
→ prompt build
→ model generates draft response
→ safety review evaluates draft
→ final response returned

## Rationale

- preserves full model output for inspection
- enables logging of unsafe drafts
- supports iterative improvement of safety rules
- avoids premature blocking of benign inputs

## Consequences

+ increased transparency
+ better debugging and observability
+ flexible rule evolution

- unsafe drafts must be handled carefully
- adds latency (additional processing step)

## Alternatives Considered

### Pre-generation filtering
Rejected:
- hides model behavior
- harder to debug false positives
- reduces system introspectability