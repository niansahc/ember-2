# Relational Orientation — Research Note

Status: Research backlog — v0.16.0+
Moved from: ADR-017 (superseded 2026-04-06)

## Concept

A per-relationship orientation layer — what Ember has learned to do differently in a specific relationship over time. Not facts about the person. The system's learned relational stance.

Distinct from Lodestone (user values) and profile memory (user facts). The test: if you removed this layer, would Ember's responses change in novel situations? If yes, the orientation is real.

## Theoretical Foundation

The prior ADR-017 draft contains the full theoretical grounding — Bowlby IWMs, Interdependence Theory, diagnostic situation detection, Kirk et al. socioaffective alignment. That research is sound. The concept was misnamed as Lodestone; it is a distinct architectural layer.

## Sequencing Dependency

Requires:
- ADR-016 (nature layer) — stable self to orient from
- ADR-017 (lodestone) — value context to ground relational scripts against
- v0.16.0+ maturity

## Open Questions

- Is this redundant with good retrieval + reflection? Deep research finding: not redundant, but requires v0.15.0+ maturity to implement correctly.
- Minimum viable version?
- Relationship to deviation memory at v0.16.0?

## Prior Work

See the superseded ADR-017 draft (archived in git history) for full schema, update rules, differentiation safeguard, and failure mode analysis.
