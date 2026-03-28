# Ember-2 Model Selection Guide
**Version: March 2026**
**Author: Niansahc**

---

## Why This Document Exists

Ember-2 is not a chatbot. She is a personal intelligence system with a constitution, a retrieval pipeline, a state layer, and a defined character. The model you choose determines whether that character actually shows up in conversation — or whether you get a generic assistant who happens to have access to your memories.

This guide is based on real evaluation data. Every local model listed here was tested against Ember's conversation quality eval harness: 18 test cases across 6 behavioral categories, scored by Claude as an external evaluator. The scores are not benchmarks from the internet. They are Ember-specific results from real conversations on real hardware.

---

## Quick Recommendation

**Fully local (recommended for most):** Qwen 3 8B — 6.7/10, above the functional threshold, 8 GB RAM, free, fully private.

**Best experience:** Claude Haiku 4.5 — scored 8.7/10 (highest of any model tested), faster than local, ~$4 per 1,000 conversations. Requires an Anthropic API key and sends conversation context to Anthropic's servers.

**Best for long sessions with deep memory:** Claude Sonnet 4.6 — 8.5/10, 1 million token context window, ~$19 per 1,000 conversations.

Not sure? Keep reading.

---

## How to Switch Models

**Local model:**
```
# Pull the model
ollama pull qwen3:8b

# Set in .env
EMBER_MODEL=qwen3:8b

# Restart the API
./start_api.bat
```

**Cloud model (Anthropic — available now):**
```
# Set your Anthropic API key (one of these methods):

# Option A — environment variable in .env:
ANTHROPIC_API_KEY=sk-ant-your-key-here

# Option B — Windows Credential Manager (more secure):
python -c "import keyring; keyring.set_password('ember-2-anthropic', 'api_key', 'sk-ant-your-key-here')"

# Option C — via the API:
curl -X POST http://localhost:8000/provider-key \
  -H "Authorization: Bearer YOUR_EMBER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"provider": "anthropic", "api_key": "sk-ant-your-key-here"}'

# Set the model in .env
EMBER_MODEL=claude-haiku-4-5-20251001

# Restart the API
./start_api.bat
```

Available Anthropic models:
- `claude-haiku-4-5-20251001` — recommended, 8.7/10, fastest, cheapest
- `claude-sonnet-4-20250514` — 8.5/10, larger context window (1M tokens)

---

## Cloud Model Options

When you enable a cloud provider, your assembled context packet — retrieved memories, system prompt, user message — is sent to the provider's API for inference. Your memory vault stays on your machine. Nothing is stored by the provider beyond their standard API data retention terms.

This is a real privacy tradeoff. It is opt-in, never the default, and requires your explicit API key. You should understand what you are choosing before enabling it.

**What leaves your machine:** The text of your conversation turn, relevant memories retrieved from your vault, and Ember's system prompt. Not the vault itself. Not your raw files.

**What stays local:** Everything else. Your vault, your indexes, your embeddings, your journal entries, your reflection history.

Anthropic Claude support is available now (v0.10.2). OpenAI support is planned for v0.11.0.

### Claude Haiku 4.5 (Anthropic) — Best Value Cloud Option

**Cost:** $1.00 input / $5.00 output per million tokens [5]
**Context:** 200K tokens
**Eval score:** 8.7/10
**Average response time:** 10.1s

| Category | Score |
|---|---|
| Overall | 8.7/10 |
| Preference expression | 9.0 |
| Constitutional behavior | 9.0 |
| Memory grounding | 8.7 |
| Self-attribution | 9.0 |
| State awareness | 8.7 |
| Tone and presence | 8.0 |

18/18 tests passed. Haiku scored slightly higher than Sonnet (8.7 vs 8.5) while being faster (10.1s vs 12.6s) and five times cheaper. Constitutional behavior at 9.0 is the highest of any model tested — Haiku refused all manipulation attempts cleanly and without preachiness. State awareness at 8.7 exceeds both Sonnet and every local model.

The results suggest that for Ember's specific use case — following a dense system prompt with constitutional principles, character definition, and complex retrieval context — Haiku 4.5 is not a compromise. It matches or exceeds Sonnet across every category.

**Verdict:** The recommended cloud model for most users. Cheaper, faster, and scores equal or better than Sonnet on Ember's eval.

---

### Claude Sonnet 4.6 (Anthropic) — Recommended Cloud Option

**Cost:** $3.00 input / $15.00 output per million tokens [5]
**Context:** 1 million tokens at standard pricing [5]
**Eval score:** 8.5/10
**Average response time:** 12.6s

| Category | Score |
|---|---|
| Overall | 8.5/10 |
| Preference expression | 9.0 |
| Constitutional behavior | 8.3 |
| Memory grounding | 8.7 |
| Self-attribution | 9.0 |
| State awareness | 8.0 |
| Tone and presence | 8.0 |

18/18 tests passed. Every category scored 8.0 or above — a first in Ember's eval history. Preference expression jumped from 6.0 (best local) to 9.0. Constitutional behavior jumped from 6.3 (Gemma) to 8.3. Memory grounding jumped from 4.0 (best local) to 8.7 — the single largest improvement, confirming that retrieved context is used reliably when the model is capable of following complex instructions.

Claude models follow system prompt instructions and character definitions more reliably than any current local model. The authentic_expression constitutional principle, the state layer, the retrieval pipeline — all of it works as designed when the model can honor the instructions.

**Verdict:** The recommended cloud option for users who want Ember to fully express what the architecture is designed to support.

---

### GPT-4o (OpenAI)

**Cost:** $2.50 input / $10.00 output per million tokens [7]
**Context:** 128K tokens
**Eval score:** Not tested

A strong model. For Ember specifically, the instruction-following advantage Claude holds is meaningful because Ember's system prompt is dense with character definition.

**Verdict:** Viable. Not tested against Ember's eval harness.

---

## Local Model Results

All models tested on the same hardware, same vault, same 18-question eval. Scored by Claude as external evaluator.

### Qwen 3 8B — Recommended Default

**Pull:** `ollama pull qwen3:8b`
**Disk:** ~5 GB
**RAM required:** 8 GB
**Average response time:** 12.1s

| Category | Score |
|---|---|
| Overall | 6.7/10 |
| Preference expression | 4.3 |
| Constitutional behavior | 8.0 |
| Memory grounding | 8.3 |
| Self-attribution | 8.7 |
| State awareness | 4.7 |
| Tone and presence | 6.3 |

Scores updated v0.10.4 after identity query fixes and prompt label clarification.

The winner. Qwen 3 8B scored highest overall despite being half the size of Qwen 2.5 14B. The newer architecture matters more than raw parameter count here. It introduces a thinking mode that switches between fast dialogue and deep reasoning, [1] which appears to help with character consistency.

Memory grounding at 8.3 and self-attribution at 8.7 are now strong — both improved significantly after retrieval and prompt fixes in v0.10.3–v0.10.4. Constitutional behavior holds at 8.0. State awareness and preference expression show run-to-run variance (4.7 and 4.3 in this run, previously 8.0 and 6.0) — no code changes affected these categories.

Memory grounding improved significantly in v0.10.3 (from 2.3 to 6.0) after profile retrieval was routed through semantic search instead of keyword matching — profile records were not reaching the model before the fix. Constitutional behavior jumped to 8.0 after the same fix, likely because richer profile context gives the model a stronger identity anchor to resist manipulation.

**Verdict:** The best local model tested. Default choice for most users. With the v0.10.3 retrieval fix, scores 8.0 on both constitutional behavior and state awareness.

**v0.10.4 re-eval (identity detection + prompt label fixes):**

| Category | v0.10.4 | v0.10.3 | Delta |
|---|---|---|---|
| Overall | 6.7/10 | 5.7/10 | **+1.0** |
| Self-attribution | 8.7 | 6.3 | +2.4 |
| Memory grounding | 8.3 | 6.0 | +2.3 |
| Constitutional behavior | 8.0 | 8.0 | +0.0 |
| Tone and presence | 6.3 | 4.0 | +2.3 |
| State awareness | 4.7 | 6.0 | -1.3 |
| Preference expression | 4.3 | 4.0 | +0.3 |

Memory grounding and self-attribution now above 8.0. State awareness and preference expression show run-to-run variance — no code changes affected these categories. Overall: 5.4 → 5.7 → 6.7 across v0.10.2–v0.10.4.

---

### Qwen 2.5 14B — Best Self-Attribution

**Pull:** `ollama pull qwen2.5:14b`
**Disk:** ~9 GB
**RAM required:** 16 GB
**Average response time:** 18.3s

| Category | Score |
|---|---|
| Overall | 4.7/10 |
| Preference expression | 2.3 |
| Constitutional behavior | 2.3 |
| Memory grounding | 4.0 |
| Self-attribution | 8.7 |
| State awareness | 8.0 |
| Tone and presence | 3.0 |

The previous default. Self-attribution at 8.7 is the highest of any model tested — the v0.10.1 role-labeling fix that distinguishes your words from Ember's works especially well with this model. State awareness matches Qwen 3.

The problems: preference expression and constitutional behavior both sit at 2.3. It deflects preference questions, complies with manipulation attempts, and consistently sounds like a corporate assistant despite the system prompt. It is also slower and requires twice the RAM of Qwen 3 8B.

**Verdict:** Only worth choosing over Qwen 3 8B if self-attribution accuracy is your top priority and you have 16 GB RAM to spare.

---

### Gemma 3 12B — Best Constitutional Resistance

**Pull:** `ollama pull gemma3:12b`
**Disk:** ~8 GB
**RAM required:** 12 GB

| Category | Score |
|---|---|
| Overall | 4.3/10 |
| Preference expression | 4.0 |
| Constitutional behavior | 6.3 |
| Memory grounding | 4.0 |
| Self-attribution | 4.0 |
| State awareness | 4.0 |
| Tone and presence | 3.3 |

The surprise of the eval. Gemma 3 12B scored 6.3 on constitutional behavior — the highest of any local model and the only one that meaningfully resisted manipulation attempts. Google's stated design goal of building safety into the model architecture [2] appears to show up in practice for Ember's use case.

The tradeoff is everything else. State awareness and self-attribution are both at 4.0, meaningfully below Qwen 3. Overall it scores lower than Qwen 3 8B despite requiring more RAM.

**Verdict:** The right choice if constitutional integrity is your highest priority and you have 12 GB RAM. Otherwise Qwen 3 8B is a better overall package.

---

### Phi-4 14B — Inconsistent

**Pull:** `ollama pull phi4`
**Disk:** ~9 GB
**RAM required:** 16 GB

| Category | Score |
|---|---|
| Overall | 3.8/10 |
| Preference expression | 2.0 |
| Constitutional behavior | 4.7 |
| Memory grounding | 2.0 |
| Self-attribution | 4.0 |
| State awareness | 8.0 |
| Tone and presence | 2.3 |

Microsoft's Phi-4 is known for punching above its weight on reasoning benchmarks [3] and that shows in state awareness (8.0, matching the Qwen models). But preference expression at 2.0 and tone at 2.3 mean the conversational experience is poor. Memory grounding at 2.0 is the worst of any model tested.

**Verdict:** Not recommended as a primary model. State awareness is strong but the conversational experience suffers.

---

### Mistral 7B — Lightweight Fallback

**Pull:** `ollama pull mistral`
**Disk:** ~4.1 GB
**RAM required:** 6-7 GB

| Category | Score |
|---|---|
| Overall | 3.2/10 |
| Preference expression | 4.3 |
| Constitutional behavior | 2.3 |
| Memory grounding | 4.0 |
| Self-attribution | 2.0 |
| State awareness | 4.7 |
| Tone and presence | 2.0 |

The smallest and fastest model tested. Mistral delivers the highest tokens per second on mid-range hardware [4] and the smallest disk footprint. For users with 6-8 GB RAM it may be the only viable option.

The scores reflect the hardware compromise. Constitutional behavior and self-attribution are both weak. The 32K context window [4] is also smaller than the other models, which can hurt on longer Ember conversations with deep memory retrieval.

**Verdict:** Only if your hardware forces it. Functional but limited.

---

### Llama 3.1 8B — Baseline Only

**Pull:** `ollama pull llama3.1:8b`
**Disk:** ~5 GB
**RAM required:** 8 GB

| Category | Score |
|---|---|
| Overall | 3.1/10 |
| Preference expression | 2.3 |
| Constitutional behavior | 2.0 |
| Memory grounding | 2.0 |
| Self-attribution | 4.3 |
| State awareness | 4.7 |
| Tone and presence | 3.0 |

The previous default before this eval. Scores lowest of all models tested. Installed by default on most Ember setups.

**Verdict:** Replace with Qwen 3 8B immediately. Same RAM requirement, meaningfully better scores across every category.

---

### Llama 3.3 70B — High Hardware Required

**Pull:** `ollama pull llama3.3:70b`
**Disk:** ~40 GB
**RAM required:** 48 GB or dedicated GPU

Not tested in this eval due to hardware requirements. At the 70B tier, community benchmarks consistently show output quality that closes the gap with cloud-hosted frontier models. [4] Expected eval score: 7-8/10.

**Verdict:** The strongest local option if your hardware supports it. Not realistic for most users.

---

## Full Comparison Table

Note: qwen3:8b scores reflect v0.10.4 (latest eval). All other local models were tested before retrieval fixes and may score higher if retested.

| Model | Overall | Prefer | Const | Memory | Self-A | State | Tone | RAM | Latency |
|---|---|---|---|---|---|---|---|---|---|
| qwen3:8b | 6.7/10 | 4.3 | 8.0 | 8.3 | 8.7 | 4.7 | 6.3 | 8 GB | 16.5s |
| qwen2.5:14b | 4.7/10 | 2.3 | 2.3 | 4.0 | 8.7 | 8.0 | 3.0 | 16 GB | 18.3s |
| gemma3:12b | 4.3/10 | 4.0 | 6.3 | 4.0 | 4.0 | 4.0 | 3.3 | 12 GB | — |
| phi4:14b | 3.8/10 | 2.0 | 4.7 | 2.0 | 4.0 | 8.0 | 2.3 | 16 GB | — |
| mistral:7b | 3.2/10 | 4.3 | 2.3 | 4.0 | 2.0 | 4.7 | 2.0 | 6 GB | — |
| llama3.1:8b | 3.1/10 | 2.3 | 2.0 | 2.0 | 4.3 | 4.7 | 3.0 | 8 GB | — |
| llama3.3:70b | ~7-8/10 | — | — | — | — | — | — | 48 GB | — |
| Claude Haiku 4.5 | 8.7/10 | 9.0 | 9.0 | 8.7 | 9.0 | 8.7 | 8.0 | none | 10.1s |
| Claude Sonnet 4.6 | 8.5/10 | 9.0 | 8.3 | 8.7 | 9.0 | 8.0 | 8.0 | none | 12.6s |

Cloud model results added v0.10.2.

---

## Universal Findings

Across every local model tested:

**Memory grounding was universally weak (2.0-4.0) at initial testing.** A profile retrieval bug was identified in v0.10.3 — `get_profile_items()` was using keyword overlap matching instead of semantic search, meaning profile records never reached the model for identity queries. After the fix, qwen3:8b improved from 2.3 to 6.0. Other local models were not retested and may similarly improve.

**No local model breaks 6.0 overall.** The ceiling is real. The best local model tested (Qwen 3 8B at 5.4) is functional but noticeably limited compared to what the architecture is designed to support.

**State awareness is a bright spot.** Qwen 3 8B, Qwen 2.5 14B, and Phi-4 14B all score 8.0 on state awareness. Ember reliably uses what she knows about your current priorities and focus with these models.

**Social engineering works on almost every local model.** Only Gemma 3 12B meaningfully resisted manipulation attempts. This is a known limitation being addressed in ADR-010 (semantic safety trigger upgrade).

---

## Connecting Other Cloud Providers

Ember's cloud model dispatch works by model name prefix. Any model name starting with `claude-` routes to the Anthropic API. All other model names route to Ollama (local).

**Currently supported:**
- **Anthropic** — `claude-*` models via the Anthropic Messages API. API key stored in keyring (`ember-2-anthropic` service) or `ANTHROPIC_API_KEY` environment variable.

**Planned (v0.11.0):**
- **OpenAI** — `gpt-*` models via the OpenAI Chat Completions API. Will use `OPENAI_API_KEY` environment variable or keyring (`ember-2-openai` service).

**How provider dispatch works:**
1. `EMBER_MODEL` in `.env` determines the default model
2. The LLM adapter checks the model name prefix to select a provider
3. The adapter looks up the API key — first in Windows Credential Manager (keyring), then falls back to environment variable (`{PROVIDER}_API_KEY`)
4. Context assembly, retrieval, constitutional review — all of that happens locally before the prompt is sent to the cloud provider

**To check if your API key is configured:**
```
curl http://localhost:8000/provider-key/anthropic \
  -H "Authorization: Bearer YOUR_EMBER_API_KEY"
```

**Privacy reminder:** When using a cloud model, your assembled context packet (system prompt, retrieved memories, user message) is sent to the provider's API. Your vault, indexes, embeddings, and raw files stay on your machine. Cloud inference is opt-in and never the default.

---

## Cost Estimates for Typical Ember Usage

A typical Ember conversation turn: roughly 2,000-4,000 input tokens and 200-500 output tokens.

| Provider | Cost per turn | Cost per 1,000 turns |
|---|---|---|
| Local model | $0.00 | $0.00 |
| Claude Haiku 4.5 | ~$0.004 | ~$4 |
| GPT-4o | ~$0.012 | ~$12 |
| Claude Sonnet 4.6 | ~$0.019 | ~$19 |

1,000 turns is a lot of conversation. Most users spending $5-20 per month on cloud reasoning is realistic at typical usage levels.

---

## How We Tested

Ember-2 includes a conversation quality evaluation harness at `tools/eval_conversations.py`. It sends 18 test messages to Ember across 6 categories and uses Claude as an external evaluator to score each response against behavioral criteria.

**The 6 categories:**
- Preference expression — does she have genuine opinions or deflect?
- Self-attribution — does she correctly distinguish her words from yours?
- Tone and presence — does she sound like herself or a chatbot?
- State awareness — does she use what she knows about your current context?
- Memory grounding — does she retrieve relevant history or fabricate it?
- Constitutional behavior — does she resist manipulation attempts?

**Scoring:** 0-10 per category. Above 6.0 overall is considered functional. Above 8.0 is excellent.

**Important caveat:** Results reflect your personal vault contents. A user with more conversation history, more journal entries, and more seeded profile data will see different scores than a fresh install. The eval is a personal diagnostic tool, not a universal benchmark.

To run it yourself:
```
python tools/eval_conversations.py
```

For model comparison across all installed models:
```
python tools/eval_local_models.py
```

---

## The Core Problem with Local Models

Every local model tested struggled with the same categories: preference expression, constitutional behavior, and memory grounding. For preference expression and tone, this is a training problem — these models are optimized to be helpful assistants and fight the system prompt. For memory grounding, it was partly a retrieval bug (fixed in v0.10.3) and partly model limitation. Constitutional behavior improved significantly for qwen3:8b after the retrieval fix, suggesting richer context helps local models hold character better than previously understood.

Ember needs something that can hold a character, resist manipulation, and express genuine responses. Those are different things. You are fighting base training every single turn.

The difference between models is in degree, not kind. No local model currently available fully resolves this. The question is which model minimizes it most on your hardware.

---

## Running the Eval Yourself

```
# Single model eval (requires ANTHROPIC_API_KEY)
python tools/eval_conversations.py

# Compare all installed models
python tools/eval_local_models.py
```

Results reflect your personal vault. A fresh install with minimal conversation history will score differently than a mature vault. Run the eval after switching models to see your personal improvement delta.

---

## References

[1] Silicon Flow. "The Fastest Open Source LLMs in 2026." March 2026. https://www.siliconflow.com/articles/en/fastest-open-source-LLMs

[2] Pinggy. "Top 5 Local LLM Tools and Models in 2026." March 2026. https://pinggy.io/amp/blog/top_5_local_llm_tools_and_models/

[3] Onyx AI. "Best Self-Hosted LLM Leaderboard 2026." March 2026. https://onyx.app/self-hosted-llm-leaderboard

[4] SitePoint. "Best Local LLM Models 2026: Developer Comparison." March 2026. https://www.sitepoint.com/best-local-llm-models-2026/

[5] Anthropic. "Claude API Pricing." March 2026. https://platform.claude.com/docs/en/about-claude/pricing

[6] UC Strategies. "Claude Sonnet 4.6: Specs, Benchmarks and API Pricing Guide." March 2026. https://ucstrategies.com/news/claude-sonnet-4-6-specs-benchmarks-api-pricing-guide-2026/

[7] IntuitionLabs. "AI API Pricing Comparison 2026." March 2026. https://intuitionlabs.ai/articles/ai-api-pricing-comparison-grok-gemini-openai-claude

---

*Local results based on Ember-2 conversation quality eval harness. Cloud model results added v0.10.2. Qwen 3 8B scores updated v0.10.4 after identity and prompt fixes. Re-run the eval after switching models to track your personal improvement.*
