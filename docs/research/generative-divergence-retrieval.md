# Generative Divergence Retrieval — Structural Resonance for Creative Collaboration

**Status:** Research note
**Target version:** v0.16.0 (no build items yet)
**Date:** 2026-04-10

---

## Summary

A retrieval mode that surfaces vault content with low semantic similarity but shared structural pattern — for creative collaboration where topical relevance is the wrong criterion.

Standard retrieval finds things that are *about* the same topic. Generative divergence retrieval finds things that *work the same way* across different topics — structural resonance across domains.

Example: a user working on a team dynamics problem might benefit from retrieving a vault record about garden ecosystem management. The topics are unrelated, but the structural pattern (interdependent systems where removing one element cascades) is shared. Standard semantic search would never surface this connection.

---

## Architecture

### Structural tags at write time

Structural tags are added to vault records at ingestion via LLM extraction — one Ollama call per record, not at retrieval time. This amortizes the cost: each record is tagged once at write, then the tags are available for every subsequent query.

Tags are stored in record metadata:
- `structural_tags`: list of strings, max 3 per record (e.g. `["interdependent_system", "cascading_removal", "equilibrium_disruption"]`)
- `domain_surface`: string, the surface-level category (e.g. `"team_dynamics"`, `"gardening"`, `"architecture"`)

### Creative retrieval at query time

When the user explicitly invokes creative retrieval mode:

1. Embed the query and retrieve top-N candidates from the vector index (standard path)
2. **Cosine similarity ceiling**: exclude any candidate with similarity > 0.65. High similarity means topical relevance — that's standard retrieval, not divergence retrieval.
3. **Structural tag match filter**: from the remaining candidates, select those with 2+ shared `structural_tags` with the query's extracted structural pattern.
4. Return the filtered set, ranked by structural tag overlap count.

### Constraints

- **Explicit user-invoked mode only** — not always-on. The cost of surfacing structurally-resonant but topically-irrelevant content on a normal query is confusion and degraded trust. This mode activates only when the user explicitly requests it.
- **No new embeddings, no new vector index** — operates as a metadata filter over existing retrieval candidates. The structural tags live in record metadata, not in a separate index.
- **No real-time LLM calls at retrieval** — all tagging happens at write time. Retrieval is pure metadata filtering.

---

## Prerequisites before implementation

1. **Design the structural pattern taxonomy** (15-20 patterns) for Ember's actual use cases. The taxonomy must be empirically grounded in the vault's real content, not theoretically derived. Run a sample of 100 vault records through an LLM extraction pass and cluster the results to find natural structural categories.

2. **Add `structural_tags` and `domain_surface` fields to the ingestion pipeline** — extends the record metadata schema. Must be compatible with existing records (fields are optional, absent = not tagged).

3. **Batch backfill existing vault records** — one-time migration script to tag existing records. Requires careful rate limiting against Ollama to avoid blocking normal API traffic.

---

## Key failure modes

1. **False structural similarity** — tags too broad. If "system" and "change" are structural tags, almost everything matches. The taxonomy needs to be specific enough to be discriminating.

2. **Same-domain matches defeating cross-domain intent** — if the similarity ceiling (0.65) is too high, topically-related records sneak through. If too low, the candidate pool is too small to find structural matches.

3. **Taxonomy coverage gaps** — some vault content doesn't map to any structural pattern in the taxonomy. These records are invisible to divergence retrieval. Acceptable if the taxonomy covers 70%+ of records.

4. **Emotional content in unexpected connections** — a structurally-resonant record from a difficult personal experience surfacing during a work brainstorming session could be jarring or inappropriate. This is the primary reason for the explicit-mode-only constraint.

5. **LLM tagging noise on short records** — records under ~50 words may not contain enough structural signal for reliable tagging. Consider a minimum content length gate for structural tag extraction.

---

## References

- Deep Research synthesis, April 2026
- ADR-020 (connector architecture) — ingestion pipeline extension point for structural tag extraction
- MemPalace validity windows (TDD §50.1 Active Watch Items) — different approach to the same "what's useful beyond topical relevance" question
