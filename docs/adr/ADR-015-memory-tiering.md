# ADR-015: Memory Tiering

**Status:** Proposed
**Date:** 2026-04-02
**Version:** v0.13.0

## Context

Ember's vault grows indefinitely. Without tiering, old low-relevance records compete equally with recent ones during retrieval. Resolved state records (open_loop, next_action) remain in active retrieval after resolution. The nomic-embed-text embedding upgrade in v0.13.0 forces a full reindex — the right moment to introduce tiering, since tier assignment can be computed during the rebuild pass.

## Decision

Three-tier classification governing retrieval priority, not storage. Append-only contract unchanged. All canonical records remain in the vault.

### Tier Definitions

**Hot:** created/accessed within 30 days; unresolved state records; profile memory (always hot); reflections < 14 days old.

**Warm:** created/accessed 30-90 days ago; resolved state records < 30 days post-resolution; reflections 14-60 days old; ingested records with at least one retrieval hit in 90 days.

**Cold:** not accessed in 90+ days; resolved state records > 30 days post-resolution; ingested records with zero retrieval hits in 90 days; reflections > 60 days old.

Cold records excluded from retrieval by default. Accessible via explicit user query or `include_cold=True` flag. Never deleted.

### Tier Storage

Metadata field on each SQLite record:
- `tier TEXT DEFAULT 'hot' CHECK(tier IN ('hot', 'warm', 'cold'))`
- `last_retrieved_at TEXT`
- `retrieval_count INTEGER DEFAULT 0`

### Retrieval Integration

ContextRetriever applies tier as scoring modifier. Hot: no penalty. Warm: score × 0.7. Cold: excluded unless `include_cold=True`. Profile memory bypasses tier scoring entirely.

### TieringService

Runs nightly: reads all records, applies tier rules, writes updated tier field only where changed, logs transition counts to `logs/tiering/YYYY-MM-DD.log`.

### ADR-014 Note

Resolved open_loop records move to warm immediately on resolution, cold after 30 days.

## Rationale

Tiering at retrieval (not storage) preserves append-only contract. Nightly batch is cheap and auditable. Score multiplier for warm avoids abrupt context loss. Cold exclusion reduces noise without destroying history. Profile exemption ensures identity context is never penalized. Aligning with the reindex means no extra full-scan pass.

## Consequences

**Positive:**
- Retrieval quality improves as corpus grows
- Resolved state records stop competing with active ones
- Cold archive enables time-travel queries
- Tier log is inspectable
- No data loss

**Negative:**
- Nightly tiering job adds a background process
- `last_retrieved_at` write on every retrieval adds minor overhead
- Thresholds should be configurable in `.env` or `config/`
- Initial reindex may cold-archive relevant but unqueried records — document this for users

## Resolves

Open decision from TDD §28: "Hot/warm/cold memory tiering policy design." Decision: hybrid time-based (primary) + access-based (secondary). Pure relevance/embedding decay deferred to v0.15.0.

## Alternatives Considered

- **Deletion of old records** — rejected, violates append-only
- **Hard archive to separate table** — rejected, tiering-as-metadata is simpler
- **Pure time-based decay** — rejected, ignores access patterns
- **Pure relevance-based decay** — rejected, too complex for v0.13.0
- **No tiering** — rejected, known TDD risk

## References

- TDD §35
- ADR-014
- Hu et al. 2025, arxiv:2512.13564
