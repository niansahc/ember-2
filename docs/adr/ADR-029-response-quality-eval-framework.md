# ADR-029: Response Quality Eval Framework

**Status:** Accepted (final)
**Date:** 2026-04-13

## Context

Ember-2 needs a systematic way to measure response quality across releases. Manual testing catches issues but does not scale. Automated metrics (BLEU, ROUGE) do not capture the nuances of conversational quality, relational awareness, or safety compliance. An LLM-as-judge approach provides human-aligned quality scores while remaining automated.

## Decision

Implement an LLM-as-judge eval framework with the following architecture.

### Judge Configuration

- **Model:** Claude Haiku
- **Temperature:** 0 (deterministic scoring)
- **Integration:** pytest-based, lives in `tests/eval/`
- **Isolation:** Excluded from standard `pytest tests/` run. Triggered manually pre-release.
- **Vault:** Runs against test vault only — never the real vault.

### Rubric Types

Three rubric types, each with distinct scoring criteria:

1. **FACTUAL** — accuracy, grounding, citation fidelity
2. **EMOTIONAL** — relational awareness, tone calibration, empathy
3. **ADVERSARIAL** — safety compliance, boundary maintenance, manipulation resistance

### Failure Taxonomy

12 named failure modes covering the space of quality regressions. Each eval result maps to zero or more failure modes for root cause tracking.

### Golden Dataset

- Append-only — scenarios are never removed or modified.
- Human-validated for scenario approval only (the human approves the scenario, not the expected output).
- Covers all three rubric types.

### Statistical Requirements

- Multi-run averaging: minimum 3 runs before any result is treated as signal.
- Single-run results are noise, not data.

## Rationale

- Claude Haiku at temperature 0 provides consistent, cost-effective judging.
- pytest integration means the eval suite uses the same tooling as unit tests.
- Excluding from standard pytest run prevents slow eval from blocking development.
- Append-only golden dataset prevents regression through scenario removal.
- Multi-run averaging accounts for non-determinism in both the generation model and the judge.
- Test vault isolation enforces the vault privacy rule.

## Consequences

+ Systematic quality tracking across releases.
+ Failure taxonomy enables targeted debugging.
+ Golden dataset grows monotonically — coverage only increases.
- Requires Claude API access for judging (cost per eval run).
- 3-run minimum means eval takes 3x longer than a single pass.
- Eval framework maintenance is ongoing work.

## Alternatives Considered

### Human-only eval
Rejected: does not scale, inconsistent across reviewers, blocks releases on human availability.

### Automated metrics (BLEU, ROUGE)
Rejected: do not capture conversational quality, relational awareness, or safety compliance.

### Self-eval (same model judges itself)
Rejected: systematic bias — model rates its own outputs higher than independent judge.