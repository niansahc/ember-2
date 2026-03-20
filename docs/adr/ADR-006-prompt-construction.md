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

Implement a structured prompt builder with explicit sections:

1. System Prompt
   - loaded from static file (ember_system_prompt.txt)
   - defines behavior, tone, and reasoning rules

2. Memory Section
   - top-N retrieved memory items
   - injected as plain text blocks
   - labeled clearly as memory/context

3. User Input
   - raw user message appended at the end

Final structure:

[System Prompt]

[Memory Context]
- memory 1
- memory 2
...

[User Input]
<user message>

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
