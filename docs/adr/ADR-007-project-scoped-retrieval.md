# ADR-007: Project-Scoped Retrieval

**Status:** Accepted
**Date:** 2026-03-27

## Context

Ember-2 supports grouping conversations into projects. When a user is
working within a project context, Ember should prefer memories and
reflections that belong to that project. Without this, a user working on
"Ember Development" gets memories from all contexts — work, personal,
other projects — weighted equally by semantic similarity alone.

The UI already assigns conversations to projects (via session metadata),
and the project CRUD endpoints exist. The missing piece is retrieval
awareness: the context assembly pipeline needs to know the active project
and use it during ranking.

## Decision

**Boost, not filter.** When a project_id is active:

- Memory and reflection items whose metadata contains a matching
  `project_id` receive a +0.15 score boost in the ContextRanker.
- All other items remain in the candidate pool at their original scores.
- If no project is active (project_id is None), ranking is unchanged.

**project_id written at turn level.** Each conversation turn's metadata
now includes `project_id` (if the session belongs to a project). This
means individual memory records carry their project affiliation and can
be boosted during retrieval without per-item session lookups.

**Boost value: 0.15.** This is meaningful (comparable to a recency boost
for items within the last week, or the user-authored content bonus) but
not overwhelming. A highly relevant memory from another project can still
outrank a less relevant memory from the active project.

## Implementation

1. `openai_adapter.py` — resolves project_id from the session record
   after `_ensure_session()`, passes it to `context_service.build_context()`,
   and writes it to conversation turn metadata.

2. `context/service.py` — accepts `project_id` parameter, calls
   `ranker.apply_project_boost()` after `apply_policy()` and before
   `rank()`.

3. `context/ranker.py` — new `apply_project_boost(items, project_id)`
   method. Adds 0.15 to score for items with matching project_id.
   Returns items unchanged if project_id is None.

4. Conversation records in the vault now carry `project_id` in metadata
   alongside `session_id`, `role`, and `content_kind`.

## Rationale

**Why boost, not filter?**

Filtering would mean a user in "Work" never sees their personal journal
entries, even if they're directly relevant to the question. Ember's value
is in cross-domain pattern recognition — filtering destroys that.

Boosting preserves general recall while surfacing project-relevant
memories first. A user asking "what have I been working on?" inside the
Ember Development project will see Ember-related memories ranked higher,
but important blockers or reflections from other contexts still surface.

**Why 0.15?**

- User-authored content bonus: +0.12
- Recency boost (within 7 days): +0.18
- Conversation type bonus: +0.10
- Project boost: +0.15

The project boost is in the same range as these existing signals —
significant enough to reorder results, not so large that it dominates.

**Why write project_id at turn level?**

The alternative is to look up each candidate's session → get project_id
at retrieval time. This requires reading the session directory for every
candidate item during ranking — expensive and fragile. Writing project_id
once at turn time makes retrieval a simple metadata check.

## Consequences

- Conversations in projects will produce higher-quality context when
  the user is working within that project.
- Old conversation records (written before this change) won't have
  project_id in their metadata. They won't be boosted, but they
  won't be penalized either — they rank normally.
- The boost is passive and invisible to the user. There's no UI
  indicator that project-scoped retrieval is active.
- Future work: project-scoped reflections (daily reflection filtered
  to a project's conversations).

## Alternatives Considered

| Alternative | Why rejected |
|---|---|
| Filter to project only | Destroys cross-domain recall |
| Higher boost (0.3+) | Overwhelms other ranking signals |
| Session-level lookup at retrieval | Too expensive per-candidate |
| Store project_id only on session, not turns | Requires session lookup during ranking |
