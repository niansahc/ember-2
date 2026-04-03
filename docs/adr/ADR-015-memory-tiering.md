# ADR-015: Memory Tiering

**Status:** Proposed
**Date:** 2026-04-02
**Version:** v0.13.0

## Context

Ember's vault grows indefinitely. Without tiering, old low-relevance records compete equally with recent ones during retrieval. Resolved state records (open_loop, next_action) remain in active retrieval after resolution. The nomic-embed-text embedding upgrade in v0.13.0 forces a full reindex — the right moment to introduce tiering, since tier assignment can be computed during the rebuild pass.

Research context: The Generative Agents paper (Park et al., UIST 2023) established the field default of exponential recency decay, but its decay parameters were arbitrary -- tuned to a simulated 16-hour game day with no empirical basis for real-world personal knowledge systems. Hou et al. (2024) demonstrated that pure recency makes a known error: it prefers frequently-seen shallow interactions over substantively important ones that appear less often. The ACT-R cognitive architecture provides the strongest theoretical grounding: base-level activation of a memory trace is a function of both recency and frequency of use -- recency-weighted frequency, not pure recency. MemoryBank implements this directly: each retrieval resets the recency clock and increments a frequency counter. MemoryOS (EMNLP 2025) implements a continuous heat score combining visit count and recency factor rather than calendar-threshold archival. The "Forgetful but Faithful" paper (Dec 2025) confirms that LRU (pure recency) is only optimal when time decay dominates -- for personal knowledge, records encoding durable identity and relationships have cross-situational value that does not decay on a calendar.

## Decision

Three-tier classification governing retrieval priority, not storage. Append-only contract unchanged. All canonical records remain in the vault.

### Tier Assignment: Composite Heat Score

Tier is determined by a composite heat score combining three signals. Calendar thresholds alone have no empirical basis for personal knowledge systems and make a known error: they discard important infrequently-accessed records in favor of recent mundane ones.

Heat score formula:
heat = (recency_score * 0.5) + (access_score * 0.3) + (importance_score * 0.2)

recency_score: exponential decay over days since last retrieval (or creation if never retrieved). Decay factor configurable via TIER_RECENCY_HALFLIFE_DAYS (default: 30). Score range 0.0-1.0.

access_score: normalized retrieval count. Computed as min(retrieval_count / TIER_ACCESS_CEILING, 1.0) where TIER_ACCESS_CEILING defaults to 10. A record retrieved 10+ times scores 1.0.

importance_score: heuristic signal for cross-situational value. For v0.13.0, derived from memory_type:
- Profile memory: 1.0 (always)
- Unresolved state records (open_loop, next_action, current_focus): 0.9
- Reflection records: 0.7
- Journal records: 0.6
- Conversation records: 0.4
- Ingested records: 0.3
Importance scoring will be refined with LLM-derived scores in a future version.

Tier thresholds (configurable via .env):
- heat >= TIER_HOT_THRESHOLD (default 0.5): Hot
- heat >= TIER_WARM_THRESHOLD (default 0.2): Warm
- heat < TIER_WARM_THRESHOLD: Cold

Hard overrides (bypass heat score):
- Profile memory: always Hot, no exceptions
- Unresolved state records: always Hot
- Resolved state records: heat score applies, but importance_score drops to 0.2 on resolution

Tier definitions:
Hot -- actively relevant, full retrieval weight. Heat score >= 0.5, or hard override applies.
Warm -- background context, reduced retrieval weight. Heat score >= 0.2 and < 0.5.
Cold -- low heat, excluded from default retrieval. Heat score < 0.2. Cold records remain in the SQLite index -- they are not removed. Cold exclusion means ContextRetriever applies a score of 0.0 to cold records during default search. They are retrievable via explicit user query or include_cold=True flag. False-cold archival recovery cost is one failed retrieval plus a fallback search -- at current scale, milliseconds. Records are never deleted.

### Tier Storage

Metadata fields on each SQLite record:
- `tier TEXT DEFAULT 'hot' CHECK(tier IN ('hot', 'warm', 'cold'))`
- `last_retrieved_at TEXT`
- `retrieval_count INTEGER DEFAULT 0`
- `importance_score REAL DEFAULT 0.5`
- `heat_score REAL DEFAULT 1.0`

importance_score is set at write time based on memory_type heuristics. heat_score is recomputed nightly by TieringService. Both are stored for auditability.

### Retrieval Integration

ContextRetriever applies tier as scoring modifier. Hot: no penalty. Warm: score × 0.7. Cold: excluded unless `include_cold=True`. Profile memory bypasses tier scoring entirely.

### TieringService

Runs nightly:
1. Read all records with their last_retrieved_at, created_at, retrieval_count, importance_score, and memory_type
2. Compute recency_score, access_score per record
3. Compute heat_score = (recency_score * 0.5) + (access_score * 0.3) + (importance_score * 0.2)
4. Apply hard overrides (profile memory, unresolved state records)
5. Assign tier based on heat thresholds
6. Write updated tier and heat_score fields only where values have changed (minimize writes)
7. Log transition counts and threshold summary to logs/tiering/YYYY-MM-DD.log

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

Open decision from TDD §28: "Hot/warm/cold memory tiering policy design." Decision: composite heat score combining recency-weighted frequency (ACT-R model) and heuristic importance. Calendar thresholds are rejected -- they have no empirical basis for personal knowledge systems and make a known error discarding important infrequently-accessed records. Pure recency is also rejected for the same reason.

## Alternatives Considered

- **Deletion of old records** — rejected, violates append-only
- **Hard archive to separate table** — rejected, tiering-as-metadata is simpler
- **Calendar threshold archival (30/90 days)** — No empirical basis for personal knowledge systems; discards important infrequently-accessed records in favor of recent mundane ones (Hou et al. 2024)
- **Pure time-based decay** — rejected, ignores access patterns
- **Pure relevance-based decay** — rejected, too complex for v0.13.0
- **No tiering** — rejected, known TDD risk

## References

- TDD §35 -- Relevance Decay and Forgetting
- ADR-014 -- Commitment Detection (resolved open_loop records are tiering candidates)
- Hu et al. (2025), "Memory in the Age of AI Agents" (arxiv:2512.13564) -- taxonomy of memory dynamics; Ember's approach maps to token-level hierarchical memory with explicit retrieval dynamics
- Park et al. (2023), "Generative Agents" (UIST 2023) -- established recency decay as field default; decay parameters are arbitrary, not empirically derived for personal knowledge systems
- Hou et al. (2024) -- spaced recall intervals outperform pure recency; pure recency prefers shallow frequent interactions over substantively important infrequent ones
- ACT-R cognitive architecture -- base-level activation as function of recency and frequency; theoretical grounding for recency-weighted frequency over pure recency
- MemoryBank -- retrieval resets recency clock and increments frequency counter; clean implementation of recency-weighted frequency
- MemoryOS (EMNLP 2025) -- continuous heat score combining visit count and recency factor; inspiration for Ember's heat score approach
- "Forgetful but Faithful" (Dec 2025) -- LRU optimal only when time decay dominates; importance-based policies preserve cross-situational value
