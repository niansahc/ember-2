# Backend Performance Audit — pre-v0.15.0

**Date:** 2026-04-12
**Version:** v0.14.2
**Method:** Static code analysis (API was in use during audit; no live profiling)
**Test count:** 1,202 passing

---

## 1. API Response Time Characteristics

### /api/health
- **Cost:** CPU only. Reads version.json (disk, cached by OS). Pings SearXNG on localhost:8888 with 3s timeout for the docker status field.
- **Expected latency:** <10ms when Docker is up, ~3s when Docker is down (timeout).

### /v1/chat/completions (non-streaming)
- **Pipeline:** payload validation → override detection (15 regex patterns) → context assembly → LLM generation → safety review (conditional) → grounding check (conditional) → 5-6 background threads → response
- **Estimated total:** 3-15s on qwen3:8b (dominated by LLM generation)
- **LLM calls:** 1 (base) + 0-1 (constitutional review, ~10-30% of requests)

### /v1/chat/completions (streaming)
- **First token:** Same pipeline up to LLM call, then tokens stream as they generate
- **Post-stream:** safety review + grounding check run after full response accumulates. If revision needed, follow-up correction is yielded.
- **Perceived latency:** First token in 1-3s, total response 5-15s

---

## 2. Context Assembly Time

**ContextService.build_context()** — the most complex pre-generation path.

| Stage | Cost Type | Estimated Time | Notes |
|-------|-----------|---------------|-------|
| classify_query() | CPU (string matching) | <1ms | O(1) heuristic |
| Embedding (4 calls) | Ollama local model | 200-600ms | 4 × embed_text(), nomic-embed-text 768-dim |
| Vector index search (3 stores) | CPU (linear scan) | 50-200ms | O(n × 768) cosine similarity per index |
| State resolution | SQLite query | <10ms | ~15 records |
| Task resolution | SQLite query | <5ms | ~5 records |
| Policy weighting + ranking | CPU | <10ms | O(n log n), n≈100 |
| Temporal decay | CPU | <5ms | 100 items × 3 parse attempts |
| Echo/meta filtering | CPU | 10-50ms | O(n × |text|) Jaccard tokenization |
| **Diversity selection** | **CPU** | **50-200ms** | **O(n²) Jaccard, dominant cost** |
| Retrieval stats update | SQLite | <5ms | 6-10 items |

**Estimated total context assembly: 350-1100ms** (excluding embedding calls: 150-500ms)

---

## 3. Retrieval Pipeline

### Embedding Lookup
- **Model:** nomic-embed-text via Ollama (local, CPU/GPU)
- **Per call:** ~50-150ms
- **Calls per request:** 4 (profile, memory, reflection, conversation — though conversation is skipped as optimization)
- **Actual:** 3 embedding calls = 150-450ms

### Index Search
- **Algorithm:** Full linear scan with cosine similarity (no HNSW/IVF/quantization)
- **Per index:** O(n × 768) where n = index size
- **For 10K-item index:** ~7.68M float operations = 50-100ms
- **Caching:** In-memory dict, warm after first query. Cold load = JSON parse of 2-50MB file.

### Ranking
- **Algorithm:** Score multiplication (policy weight × temporal decay × tier score) + O(n log n) sort
- **Items ranked:** ~100-120 candidates post-filter
- **Time:** <10ms

### Dedup + Diversity
- **Dedup:** Set-based text hash, O(n), <1ms
- **Diversity:** Round-robin across 3 type groups with Jaccard similarity to all prior selections
  - **Complexity:** O(limit² × avg_token_count)
  - **Practical:** 6-10 items selected, each compared against all prior = 15-45 Jaccard calls
  - **Time:** 50-200ms (dominant post-retrieval cost)

---

## 4. Ollama Call Latency

### Base Generation (always, synchronous)
- **qwen3:8b** with ~3,500 token prompt: **3-12s** for a typical 100-300 word response
- **claude-haiku-4-5** via cloud dispatch: **1-3s** (network + inference)
- **This is the dominant latency in every request**

### Constitutional Review (conditional, synchronous)
- **Trigger rate:** ~10-30% of requests (heuristic string matching)
- **When triggered:** 1 additional Ollama call with ~500 token MVR prompt + response text
- **Latency:** 2-5s on qwen3:8b (shorter prompt than base generation)
- **Impact:** doubles perceived latency when it fires

### Grounding Check (conditional, synchronous in streaming path)
- **Trigger rate:** depends on intent_class (identity queries, vault-grounded claims)
- **When triggered:** 1 additional Ollama call for revision
- **Latency:** 2-5s
- **Streaming penalty:** blocks re-streaming until check completes

### Buffer Compression (rare, background thread)
- **Trigger rate:** ~1/70 turns (when buffer exceeds 1,500 token threshold)
- **Cost:** 1 Ollama call, ~200 output tokens
- **Impact:** none (daemon thread, non-blocking)

---

## 5. Memory Usage

### Vault Disk Footprint (typical user)
| Component | Size |
|-----------|------|
| 3,000 conversation records | ~9 MB |
| 500 journal entries | ~1 MB |
| 200 reflections | ~600 KB |
| 500 state records | ~250 KB |
| 3 vector indexes | ~30-90 MB |
| **Total** | **~40-100 MB** |

### Process Memory at Startup
| Component | Size |
|-----------|------|
| Singletons (services, loaders) | ~680 KB |
| Nature/identity/lodestone configs | ~130 KB |
| Python runtime + FastAPI | ~50-80 MB |
| **Startup total** | **~50-80 MB** |

### Process Memory After 10 Turns
| Component | Added |
|-----------|-------|
| Vector index cache (3 indexes, warm) | +10-90 MB |
| Conversation buffer (10 turns) | +100 KB |
| State records in memory | negligible |
| **10-turn total** | **~60-170 MB** |

### Unbounded Growth Risks
- **Index cache:** Module-level dict with no eviction. All loaded indexes stay in memory. Max 150 MB if all indexes at 50 MB cap.
- **Vault size:** Append-only design means records accumulate forever. Archive script exists but must be run manually.
- **State layer:** StateExtractor creates records per turn with no ceiling. Staleness filter (7 days) prevents old records from surfacing but doesn't delete them.

---

## 6. Top 3 Slowest Paths + Recommendations

### #1: Base LLM Generation (3-12s) — 70-85% of total request time
**Location:** `src/llm/adapter.py:174` → `ollama.chat()`
**Root cause:** qwen3:8b inference speed on consumer hardware.
**Recommendation:**
- Token reduction in the system prompt. Current typical prompt is ~3,580 tokens (44% of 8K window). The dual-injection nature block (~150 tokens × 2 = 300 tokens) and the few-shot examples in the instruction section (~200 tokens) are candidates for conditional inclusion. Reducing prompt by 500 tokens could save 0.5-1s per response.
- Investigate `num_predict` cap — if Ember often generates 300+ word responses but 100 words would suffice, a generation cap with streaming could reduce tail latency.

### #2: Embedding Calls (150-450ms) — 4-15% of total
**Location:** `src/retrieval/semantic_search.py` → `embed_text()` via Ollama
**Root cause:** 3 separate embedding calls per request, each a full Ollama inference.
**Recommendation:**
- Batch embedding: combine all 3 query embeddings into a single `embed_texts()` call. The embedding is the same text for all 3 calls (the user message). Cache the result and reuse across profile, memory, and reflection retrieval. This would reduce 3 × 50-150ms to 1 × 50-150ms — saving 100-300ms per request.

### #3: Diversity Selection O(n²) Jaccard (50-200ms) — 2-7% of total
**Location:** `src/context/service.py:311-354` → `_select_diverse_memory()` + `_diversity_score()`
**Root cause:** Each candidate computes Jaccard similarity against all previously selected items. Tokenization is re-done on every comparison (no caching of token sets).
**Recommendation:**
- Pre-tokenize all candidates once before the selection loop. Store token sets in a dict keyed by item ID. Reuse during Jaccard computation. This reduces tokenization from O(limit² × |text|) to O(n × |text|) + O(limit²) comparisons on pre-built sets.
- Consider switching to a simpler diversity metric (e.g. type-based round-robin without Jaccard) since the current round-robin already provides type diversity — the Jaccard check adds diminishing returns.

### Honorable Mention: Vector Index Full Linear Scan
**Location:** `src/retrieval/vector_index.py:99-136`
**Root cause:** No approximate nearest neighbor structure (HNSW, IVF). Full O(n × 768) scan on every search.
**Impact:** Currently manageable (50-100ms for 10K items) but will degrade linearly as the vault grows. At 50K items, this becomes 250-500ms per search.
**Recommendation:** Not urgent at current vault sizes. Monitor via eval_retrieval.py latency tracking. If latency exceeds 200ms per search, consider SQLite FTS5 for lexical pre-filtering or a lightweight HNSW index (e.g. hnswlib, nmslib) alongside the existing linear search.

---

## Summary

| Metric | Value |
|--------|-------|
| Typical request latency | 4-15s (dominated by LLM generation) |
| Context assembly | 350-1100ms |
| Embedding calls | 150-450ms (3 calls, batchable to 1) |
| LLM calls per request | 1 (min) — 2 (max) |
| Background threads per request | 5-6 (non-test) |
| Prompt tokens per turn | ~3,580 (44% of 8K window) |
| Process memory (warm) | 60-170 MB |
| Vault disk (typical) | 40-100 MB |

**Bottom line:** The LLM call is the overwhelming bottleneck. Everything else is noise by comparison. The highest-impact optimization is prompt token reduction (~500 tokens = ~0.5-1s saved per response). Embedding batching (100-300ms saved) is the second-best ROI. Diversity selection caching is a code-quality win but negligible in wall-clock terms relative to the LLM call.
