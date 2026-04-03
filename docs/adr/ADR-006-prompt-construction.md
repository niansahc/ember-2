# ADR-006: Structured Prompt Construction with Explicit Context Injection

Status: Accepted
Date: 2026-03-19

## Context

Ember-2 relies on prompt construction to combine:

- system behavior (personality + rules)
- retrieved memory
- user input

Without a defined structure, prompts can drift, leading to:
- inconsistent responses
- poor memory utilization
- unpredictable model behavior

## Decision

Implement a structured prompt builder with explicit sections. Context assembly order (updated per ADR-016):

1. System Prompt — loaded from static file (ember_system_prompt.txt); defines behavior, tone, and reasoning rules
2. Date/Time — temporal grounding
3. Conversational Style — casual/balanced/thoughtful injection
4. Nature Block — Ember's nature (config/nature.yaml) injected into the context packet every turn via NatureLoader.to_prompt_text(). Not in the system prompt — placed in the context packet so nature tokens are always recent and not subject to attention dilution (ADR-016, PRISM/PERSIST research).
5. State Records — current focus, open loops, tasks
6. Capabilities — task creation, etc.
7. Reflection Context — recent reflections
8. Web Search Results — if web search was triggered
9. Memory Context — retrieved records, ranked and filtered
10. Recent Conversation — conversation buffer
11. Instruction Rules — context priority and behavior rules
12. User Input — raw user message

## Rationale

- separation of concerns improves clarity
- consistent structure stabilizes model behavior
- explicit memory injection improves retrieval effectiveness
- file-based system prompt enables easy iteration

## Consequences

+ predictable and debuggable prompts
+ easier prompt iteration and tuning
+ improved memory utilization

- requires careful formatting to avoid token bloat
- context window limits must be managed
- prompt changes affect entire system behavior

## Alternatives Considered

### Inline / ad-hoc prompt construction
Rejected:
- inconsistent structure
- difficult to debug
- leads to prompt drift

### Fully dynamic system prompts
Rejected:
- reduces control over model behavior
- harder to test and stabilize
