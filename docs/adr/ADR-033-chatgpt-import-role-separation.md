# ADR-033: ChatGPT Import Role Separation

**Status:** Accepted
**Date:** 2026-04-20
**Version:** v0.17.0 (planned)

## Context

Ember ingests ChatGPT export archives via `src/ingest/importers/chatgpt.py`. The export format contains full conversation threads: both user turns and assistant (GPT) turns. Currently, both sides are chunked and embedded into the main vault index. Each chunk carries a `role` field (`user` / `assistant`) and a `content_kind` classification, but this metadata is not used to gate indexing or retrieval eligibility.

This creates two problems:

**1. Retrieval contamination.** Semantic search surfaces GPT's prose as if it were the user's memory. A query like "what have I thought about X" may return GPT's answer about X rather than anything the user said. The user's voice and GPT's voice are indistinguishable in the embedding space.

**2. State contamination.** StateExtractor processes ingested conversation turns and writes derived state records from them. State records derived from GPT responses are not distinguishable from user-authored state. They surface on state queries with staleness qualifiers, as if they represent something the user said or experienced. This is a correctness failure: Ember is asserting facts about the user that GPT said.

The root cause is a missing principle: **GPT's responses are information the user received, not memory the user made.**

Ember's purpose is to know the user. A personal intelligence system should capture what the user said, asked, decided, felt, and did — not what a language model said in response. Mixing the two degrades both retrieval quality and state fidelity.

## Decision

Split ChatGPT import processing by role at the chunking stage:

**User turns** (`role: user`) → embedded and indexed in the main vault as first-class memory. Eligible for semantic retrieval and all retrieval policies. Available to StateExtractor only with an `ingest_source: chatgpt` flag that prevents live state extraction (see StateExtractor gating, below).

**Assistant turns** (`role: assistant`) → stored flat in the DB as non-embedded reference context. Linkable by `doc_id` and `conversation_id` for thread reconstruction. Not embedded. Not surfaced in semantic search. Not processed by StateExtractor.

**StateExtractor gating** → StateExtractor must never run on ingested content of any kind. Gate on a `is_live_turn` flag set only for turns originating from live Ember conversations. This applies to all ingest sources, not just ChatGPT. (Separate fix, same release.)

## Rationale

- User turns are the user's memory. They contain decisions, experiences, questions, emotional states, and self-descriptions — exactly what Ember is designed to retain and retrieve.
- Assistant turns are reference material, not autobiography. They belong alongside the conversation for context reconstruction, not in the semantic index for retrieval.
- Role metadata already exists on every chunk (`metadata.role`). This is a policy decision enforced at the pipeline level, not new infrastructure.
- Storing assistant turns flat preserves the ability to reconstruct full conversation threads when needed (e.g., "what was GPT's response about X"). This is reference lookup, not memory retrieval.
- StateExtractor contamination is a separate but co-located failure. Fixing it in the same release is correct because both failures share the same root cause: ingest content was treated as live user-authored content.

## Consequences

+ Retrieval surfaces only the user's voice on personal/state queries
+ StateExtractor state records are grounded in user-authored content only
+ Conversation threads remain reconstructable via doc_id / conversation_id linkage
+ Role metadata already present — implementation is policy enforcement, not new data modeling
+ Fixes the state contamination known issue filed against v0.17.0

- Assistant turns no longer semantically retrievable. If a user wants to find a specific GPT answer, they must query by conversation, not by semantic content. Accepted tradeoff.
- Requires re-ingestion of existing ChatGPT export data to apply retroactively. Or: assistant-role chunks already in the index can be pruned via a migration script. Decision deferred to implementation.
- Does not address other ingest sources (PDFs, Google Drive) — those are treated as reference material by source type already.

## Alternatives Considered

### Keep both sides embedded, gate at retrieval by role

Possible using the `eligible_memory_types` and `suppress_memory_types` infrastructure from ADR-018. Rejected for ChatGPT import because role is not the same as memory type — an assistant-role chunk has no natural memory type that maps to existing suppression rules. The separation is cleaner at the pipeline level than at the retrieval policy level.

Also: assistant turns in the embedding index still get embedded, which is wasteful and leaves contamination risk for any query path without explicit suppression.

### Distillation pass — extract structured memory records from user turns using LLM

Appealing long-term: run a preprocessing pass on user turns at import time, extract typed vault records (decisions, preferences, experiences, projects), write them as synthetic memory entries. Would give the highest signal-to-noise ratio.

Rejected for v0.17.0: requires a controlled offline LLM pass with verification. Getting it wrong creates unauditable bad vault records. Deferred to v0.18.0 as an optional enhanced import path.

### Ingest only user turns, discard assistant turns

Simpler but loses the ability to reconstruct conversation context. A user may want to see what GPT said in a given conversation. Storing assistant turns flat at no retrieval cost is strictly better than discarding them.

## Implementation Notes

Changes required:

1. `src/ingest/chunking.py` — `_chunk_chatgpt_document()`: split output into two lists by role. Return user chunks as standard `ChunkedDocument`; return assistant chunks as `ChunkedDocument` with `metadata.index_for_retrieval: False`.

2. `src/ingest/writers.py` (or equivalent) — skip embedding generation and vector indexing for chunks with `index_for_retrieval: False`. Write to storage for thread reconstruction only.

3. `src/state/state_extractor.py` — add `is_live_turn` gate. StateExtractor only runs when `is_live_turn` is True. All ingest paths set `is_live_turn: False`.

4. Migration (deferred): script to remove existing assistant-role ChatGPT chunks from the vector index without removing them from flat storage.

## Relationship to Other ADRs

- ADR-004 (ingestion pipeline) — this ADR adds a role-based split within the ChatGPT-specific chunking path. General pipeline stages unchanged.
- ADR-018 (intent-aware type gating) — retrieval gating by intent operates after indexing. This ADR operates before indexing. Complementary; not redundant.
- ADR-002 (append-only memory) — flat storage of assistant turns is consistent with append-only. They are stored, never mutated, and not indexed.

## Research Review (2026-04-20)

Current personal AI memory frameworks (Mem0, Letta/MemGPT, Zep) do not specifically address the problem of importing historical conversations from other AI assistants. They treat live conversations as unified streams and do not distinguish user-authored from AI-generated content at ingestion. No established standard exists for this use case.

Mem0's extraction approach — running a fact-extraction pass over conversation turns rather than indexing raw chunks — is architecturally close to the distillation approach deferred to v0.18.0 in this ADR. Its viability is supported by production deployment evidence, which validates deferring distillation as a meaningful enhancement rather than a replacement for the v0.17.0 role-split approach.

The Contextual Integrity framework (Nissenbaum, cited in ADR-018) remains the strongest theoretical grounding for this decision: assistant-generated content does not have appropriate information flow into a personal memory system designed to represent the user's own knowledge and experience.

The v0.17.0 role-split decision is not contradicted by current research and is more principled than current industry practice.

## References

- Mireshghallah, N. et al. "CIMemories." ICLR 2026. https://arxiv.org/abs/2511.14937 (via ADR-018)
- Nissenbaum, H. (2004). Contextual Integrity. Washington Law Review. (via ADR-018)
- Vectorize.io. "Mem0 vs Letta (MemGPT): AI Agent Memory Compared (2026)." https://vectorize.io/articles/mem0-vs-letta
- Letta documentation. https://docs.letta.com/concepts/memgpt/
- mem0ai/mem0. GitHub. https://github.com/mem0ai/mem0
