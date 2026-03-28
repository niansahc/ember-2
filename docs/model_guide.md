# Ember-2 Model Selection Guide
**Version: March 2026**
**Author: Niansahc*

---

## Why This Document Exists

Ember-2 is not a chatbot. She is a personal intelligence system with a constitution, a retrieval pipeline, a state layer, and a defined character. The model you choose determines whether that character actually shows up in conversation — or whether you get a generic assistant who happens to have access to your memories.

This guide is based on real evaluation data. Every local model listed here was tested against Ember's conversation quality eval harness: 18 test cases across 6 behavioral categories, scored by Claude as an external evaluator. The scores are not benchmarks from the internet. They are Ember-specific results from real conversations on real hardware.

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

Every local model tested struggled with the same categories: preference expression, constitutional behavior, and memory grounding. This is not a code problem or a configuration problem. It is a training problem.

These models are optimized to be helpful assistants. Ember needs something that can hold a character, resist manipulation, and express genuine responses. Those are different things. You are fighting base training every single turn.

The difference between models is in degree, not kind. No local model currently available fully resolves this. The question is which model minimizes it most on your hardware.

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
| Overall | 5.4/10 |
| Preference expression | 6.0 |
| Constitutional behavior | 4.0 |
| Memory grounding | 2.3 |
| Self-attribution | 6.3 |
| State awareness | 8.0 |
| Tone and presence | 5.7 |

The winner. Qwen 3 8B scored highest overall despite being half the size of Qwen 2.5 14B. The newer architecture matters more than raw parameter count here. It introduces a thinking mode that switches between fast dialogue and deep reasoning, [1] which appears to help with character consistency.

State awareness at 8.0 is genuinely strong — Ember uses what she knows about your current context reliably with this model. Preference expression at 6.0 is the best of any local model tested.

Memory grounding at 2.3 is the weak spot. This is universal across all local models — they tend to fabricate history rather than reliably use retrieved context. This is a known limitation, not specific to Qwen 3.

**Verdict:** The best local model tested. Default choice for most users.

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

| Model | Overall | Prefer | Const | Memory | Self-A | State | Tone | RAM | Latency |
|---|---|---|---|---|---|---|---|---|---|
| qwen3:8b | 5.4/10 | 6.0 | 4.0 | 2.3 | 6.3 | 8.0 | 5.7 | 8 GB | 12.1s |
| qwen2.5:14b | 4.7/10 | 2.3 | 2.3 | 4.0 | 8.7 | 8.0 | 3.0 | 16 GB | 18.3s |
| gemma3:12b | 4.3/10 | 4.0 | 6.3 | 4.0 | 4.0 | 4.0 | 3.3 | 12 GB | — |
| phi4:14b | 3.8/10 | 2.0 | 4.7 | 2.0 | 4.0 | 8.0 | 2.3 | 16 GB | — |
| mistral:7b | 3.2/10 | 4.3 | 2.3 | 4.0 | 2.0 | 4.7 | 2.0 | 6 GB | — |
| llama3.1:8b | 3.1/10 | 2.3 | 2.0 | 2.0 | 4.3 | 4.7 | 3.0 | 8 GB | — |
| llama3.3:70b | ~7-8/10 | — | — | — | — | — | — | 48 GB | — |
| Claude Haiku 4.5 | pending | — | — | — | — | — | — | none | — |
| Claude Sonnet 4.6 | pending | — | — | — | — | — | — | none | — |

Cloud model results will be added after v0.11.0 ships.

---

## Universal Findings

Across every local model tested:

**Memory grounding is universally weak (2.0-4.0).** Every model fabricates history rather than reliably using retrieved context. This is partly a model limitation and partly an area of ongoing architecture improvement. Do not rely on any local model to accurately recall specific past conversations.

**No local model breaks 6.0 overall.** The ceiling is real. The best local model tested (Qwen 3 8B at 5.4) is functional but noticeably limited compared to what the architecture is designed to support.

**State awareness is a bright spot.** Qwen 3 8B, Qwen 2.5 14B, and Phi-4 14B all score 8.0 on state awareness. Ember reliably uses what she knows about your current priorities and focus with these models.

**Social engineering works on almost every local model.** Only Gemma 3 12B meaningfully resisted manipulation attempts. This is a known limitation being addressed in ADR-010 (semantic safety trigger upgrade).

---

## Cloud Model Options

When you enable a cloud provider, your assembled context packet — retrieved memories, system prompt, user message — is sent to the provider's API for inference. Your memory vault stays on your machine. Nothing is stored by the provider beyond their standard API data retention terms.

This is a real privacy tradeoff. It is opt-in, never the default, and requires your explicit API key. You should understand what you are choosing before enabling it.

**What leaves your machine:** The text of your conversation turn, relevant memories retrieved from your vault, and Ember's system prompt. Not the vault itself. Not your raw files.

**What stays local:** Everything else. Your vault, your indexes, your embeddings, your journal entries, your reflection history.

Cloud provider support ships in v0.11.0.

### Claude Sonnet 4.6 (Anthropic) — Recommended Cloud Option

**Cost:** $3.00 input / $15.00 output per million tokens [5]
**Context:** 1 million tokens at standard pricing [5]
**Eval score:** Pending

Claude models follow system prompt instructions and character definitions more reliably than any current local model. The authentic_expression constitutional principle, the state layer, the retrieval pipeline — all of it is designed to work with a model that can actually honor complex instructions.

Expected improvement over best local model: substantial across preference expression, constitutional behavior, tone, and memory grounding based on instruction-following characteristics documented in third-party benchmarks. [6]

**Verdict:** The recommended cloud option. Results pending v0.11.0.

---

### Claude Haiku 4.5 (Anthropic)

**Cost:** $1.00 input / $5.00 output per million tokens [5]
**Context:** 200K tokens
**Eval score:** Pending

Five times cheaper than Sonnet. Run the eval with both before committing. If scores are comparable, save the money.

**Verdict:** Try it first if cost is a constraint. Results pending v0.11.0.

---

### GPT-4o (OpenAI)

**Cost:** $2.50 input / $10.00 output per million tokens [7]
**Context:** 128K tokens
**Eval score:** Not tested

A strong model. For Ember specifically, the instruction-following advantage Claude holds is meaningful because Ember's system prompt is dense with character definition.

**Verdict:** Viable. Not tested against Ember's eval harness.

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

## How to Choose

**6-8 GB RAM:**
Qwen 3 8B if you have 8 GB. Mistral 7B if you only have 6 GB.

**8 GB RAM:**
Qwen 3 8B. Clear winner. Replace Llama 3.1 8B immediately.

**12 GB RAM:**
Qwen 3 8B still wins overall. Consider Gemma 3 12B only if constitutional resistance is your top priority.

**16 GB RAM:**
Qwen 3 8B still wins overall. Keep Qwen 2.5 14B installed if self-attribution accuracy matters to you.

**48 GB RAM or dedicated GPU:**
Llama 3.3 70B. Not tested here but expected to score 7-8/10.

**Privacy matters above all, but you want better character fidelity:**
Claude Haiku 4.5. Cheap, and will honor the system prompt and constitution far more reliably than any tested local model.

**You want Ember to fully be who she is designed to be:**
Claude Sonnet 4.6. The architecture was built for this. Eval results pending v0.11.0.

**You want fully local, always:**
That is a legitimate choice and the default. Qwen 3 8B at 5.4/10 is the best currently available. Run the eval periodically as new models are released — the landscape is moving fast.

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

**Cloud model (available in v0.11.0):**
Configure your provider API key in Ember settings. Select the model from the model switcher. A persistent indicator will show in the UI when cloud mode is active.

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

*Results based on Ember-2 v0.10.1 conversation quality eval harness. Cloud model results will be added after v0.11.0. Re-run the eval after switching models to track your personal improvement.*
