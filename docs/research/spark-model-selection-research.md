# Spark Model Selection Research

**Dated: September 11, 2026. Findings only.**
**TDD Context:** model selection for the personal deployment tier, not the shipped floor.

Scope: model selection for the DGX Spark deployment tier specifically. Not the shipped-product
floor, which stays 8b-class on consumer gaming hardware and is out of scope here. Weighting is
tool calling and multi-turn agentic reliability. Ollama is a hard constraint. Local-first is
absolute.

---

## Q1. DGX Spark hardware envelope

**Source:** DGX Spark benchmark reviews (September 2026); llama.cpp_bench DGX Spark report; NVIDIA
developer forum thread 356716
**Finding:** The GB10 exposes roughly 119 GiB usable (128 GB decimal). llama.cpp reports 122,570
MiB total VRAM with 89,490 MiB free after system processes. Memory bandwidth is 273 GB/s LPDDR5x
shared between CPU and GPU. Prefill is compute-bound and strong (~1 PFLOP FP4; gpt-oss-120b MXFP4
measures 2,046 tok/s at pp4096). Decode is memory-bandwidth-bound: a dense model must read all
weights per token, so the theoretical decode ceiling is roughly 273 GB/s divided by weight bytes.
**Informs:** what fits and what runs at usable speed are different questions. A large static system
prompt is prefill, which is fast on this hardware. Per-call latency problems are decode, not
prefill.
**Trigger:** none. Hardware is fixed.

**Source:** llama.cpp_bench DGX Spark report (Q4_K_M, Qwen3 family)
**Finding:** Qwen3-32B dense Q4_K_M: ~10.7 tok/s measured against ~15 tok/s theoretical (18 GB
weight read per token). Qwen3-30B-A3B MoE Q4_K_M: ~89 tok/s. 8B dense: ~43 tok/s. The dense-vs-MoE
gap at similar parameter counts is roughly 8x and is the defining characteristic of this hardware.
**Source:** dense 27B/31B DGX Spark study
**Finding:** Qwen3.6-27B Q4_K_M: 11.85 tok/s (74% of theoretical). Gemma 4 31B Q4_K_M: 10.65 tok/s.
BF16 dense at this size: 4-5 tok/s. Dense 27-31B Q4 lands in the 10-12 tok/s band regardless of
family.
**Source:** NVIDIA forum thread 356716
**Finding:** qwen3:32b via Ollama on Spark: 9.46 tok/s eval rate, 3m44s to generate 2,122 tokens.
The same box running gpt-oss:20b via Ollama: 43.73 tok/s.
**Informs:** dense 30B-class is the wrong architecture for this box when each turn is a full
decode. Any dense 27B+ candidate lands in the same 10-12 tok/s band.
**Trigger:** none. Bandwidth constant.

**Source:** Hardware-Aware Speculative Task-to-Model Routing
**Finding:** Qwen-30B-A3B on Spark: theoretical 45 tok/s, achieved 25-35 tok/s with llama.cpp MTP.
gpt-oss-120b (40B active) on Spark NVFP4: achieved 35-50 tok/s. The same MoE on an RTX 5090
achieves 150-250 tok/s. The Spark's advantage is capacity, not speed.
**Informs:** a high-bandwidth consumer GPU decodes several times faster for any model that fits
both. The Spark's job is models that do not fit consumer VRAM.
**Trigger:** none.

**Source:** NVFP4 model card discussion (Ollama on DGX Spark)
**Finding:** Ollama has no speculative-decoding flag, so MTP is unavailable through Ollama.
Qwen3.6-27B-NVFP4 measured ~11.5 tok/s via Ollama against ~40 tok/s in the same repo's llama-server
benchmark. Ollama loads the model cleanly and fully GPU-resident; the gap is the missing MTP path.
**Informs:** for candidates shipping MTP heads, the Ollama path forfeits 3-4x decode throughput.
This is a serving-stack constraint, not a model constraint, and it is binding wherever Ollama is
required.
**Trigger:** Ollama adds speculative decoding support.

**Prompt headroom:** every candidate below fits with 40-100 GB spare. KV cache at a 9K prompt plus
32K conversation is under 2 GB for all MoE candidates with GQA/MLA. Headroom is not a fit
constraint on this hardware for anything under 100 GB weights. It is a speed constraint only.

---

## Q2. Tool-calling and agentic leaderboard, Spark-class

**Source:** Artificial Analysis tau2-Bench Telecom leaderboard (June 2026 snapshot)
**Finding:** Scores in this size range: GLM-4.7-Flash (Reasoning) 98.83%. Qwen3.6-35B-A3B 95.03%.
Qwen3.6-27B 93.86%. Qwen3.5-27B 93.57%. Qwen3.5-4B 91.52%. For reference: Qwen3.5-397B-A17B 95.32%,
Opus 4.8 94.44%, GPT-5.5 93.86%.
**Source:** arXiv:2511.08042 Table 6 (tau2-Bench Telecom, older Qwen3 generation)
**Finding:** Qwen3 32B (thinking) 30%. Qwen3 14B (thinking) 35%. Qwen3 8B 25%. Qwen3 30B-A3B
(thinking) 26%. Llama 4 Maverick 18%. Llama 4 Scout 16%.
**Informs:** this is a generational gap, not a size gap. A 4B model from the newer generation
(91.52%) beats a 32B from the older one (30%) by 61 points on the primary agentic benchmark.
**Trigger:** none. This finding stands.

**Source:** BFCL v4 leaderboards (September 2026)
**Finding:** The BFCL v4 public leaderboard has 21 models, mostly frontier. Qwen3.5-397B-A17B is
the top open-weight at 0.729. GLM-4.6 (FC thinking) multi-turn accuracy 0.680, comparable to a
frontier model at 0.684. No Spark-class (under 100 GB) model has a published BFCL v4 composite on
the public board as of this date.
**Informs:** BFCL v4 cannot rank Spark candidates directly. tau2-Bench is the usable public signal
for this size class.
**Trigger:** BFCL v4 adds Spark-class entries.

**Source:** DGX Spark benchmarks guide (August 2026, aggregating community runs)
**Finding:** Qwen3.6-35B-A3B via a non-Ollama stack at NVFP4: 218.85 tok/s, 100/100 on
Tool-Eval-Bench. Qwen3.5-27B: most consistent all-rounder on the Ollama path. 120B MoE models are
fast but hit the tool-calling quality gate harder.
**Flag:** the 218 tok/s and 100/100 tool score are NVFP4 via a non-Ollama stack. The Ollama path
for the same model measures 25-35 tok/s with no published Tool-Eval-Bench score.
**Informs:** the strongest published Spark result requires leaving Ollama. The Ollama constraint is
binding here.
**Trigger:** adoption of a llama.cpp-direct or vLLM path for this tier.

**Source:** dgx-spark-bench (May 2026; 24 models, 45 model-mode combinations, 21 prompts across 9
categories, Ollama)
**Finding:** Composite ranking (65% pass rate, 35% wall time). gpt-oss:20b thinking: 100% pass,
52.7 tok/s, 0.33s TTFT, rank 1. gpt-oss:120b thinking: 100% pass, 40.0 tok/s, rank 3.
nemotron-cascade-2:30b: 100%, 66.8 tok/s, rank 4. gemma4:26b: 96.4%, 28.3 tok/s, rank 5.
Qwen3.5:27b: 27.6 tok/s decode, rank 38/39 on composite despite the highest cloud intelligence
index of any model tested. Summary: cloud leaderboard rankings invert locally; bandwidth
constraints reorder the quality-speed tradeoff.
**Informs:** on the Ollama path specifically, gpt-oss leads on this hardware. Qwen3.5-27B has the
best reasoning and the worst wall time.
**Trigger:** none. This is an Ollama-path measurement matching the stated constraint.

**Source:** 8-model DGX Spark benchmark (February 2026, Ollama)
**Finding:** gpt-oss:120b generated invalid JSON in tool calling tests, a non-starter for agent
frameworks. Also 5-6x more verbose than a coder-tuned peer with no quality improvement. Thinking
models return empty responses via `/api/generate` because output routes to the thinking channel;
`/api/chat` returns `message.content` correctly. Q4_K_M vs Q8_0: 23% speed gap, quality difference
"nearly invisible" across 7 agent task categories.
**Conflict:** one source reports gpt-oss:120b at 100% pass rate (May 2026); another reports invalid
JSON on tool calls (February 2026). Three months apart, different test suites, possibly different
Ollama versions. The Harmony chat template for gpt-oss had known Ollama integration issues in early
2026 that were later patched. Neither result should be assumed to transfer.
**Informs:** gpt-oss:120b must be tested on the host's actual tool schemas before it is trusted.
The `/api/chat` vs `/api/generate` finding applies to any thinking-mode model.
**Trigger:** a tool-call eval on gpt-oss:120b via current Ollama.

**Source:** NVIDIA forum thread 356716
**Finding:** Qwen3-30B-A3B-NVFP4 via TRT-LLM failed tool calling (empty tool_messages, tool-call
JSON leaked into content) while the same prompts succeeded via Ollama on qwen3:32b.
**Informs:** native FC in the model does not guarantee native FC in the serving stack. "Native FC in
Ollama yes/no" must be verified per model per Ollama version, not inferred from the model card.
**Trigger:** each candidate gets a smoke test on real tool schemas before battery.

**Llama 4:** Scout (109B/17B active) tau2-Bench Telecom 16%. Maverick 18%. Both at the bottom.
Excluded on the agentic benchmark; no further evaluation warranted.

**DeepSeek-V3.x distills:** no Spark-class DeepSeek distill with native FC in Ollama surfaced in
this pass. Unresolved; requires a follow-up search if DeepSeek is to be considered.

---

## Q3. Kimi

**Source:** Moonshot Kimi-K2 repository; Unsloth K2.6 docs; Ollama deployment guides (September 2026)
**Finding:** Kimi K2 / K2.5 / K2.6: 1T total parameters, 32B active, 384 experts, MLA attention.
K2.6 adds native vision and 256K context. Full precision: ~610 GB. Q4: ~584 GB. Dynamic 2-bit:
350 GB. Dynamic 1.8-bit: 240 GB. Kimi K3 (announced July 2026): 2.8T parameters, MXFP4 checkpoint
alone is 1.56 TB.
**Finding:** Ollama serves Kimi as a cloud tag only. Prior local tags retired June 2026. There is no
Ollama-local Kimi tag.
**Finding:** No smaller Kimi variant exists. The line is K2 (1T), K2.5 (1T), K2.6 (1T), K3 (2.8T).
Community "distills" are not Moonshot releases and none surfaced with published tool-calling scores.
**Plain statement:** Kimi does not fit this hardware at any published quantization (smallest is
240 GB against 119 GiB usable). It is not Ollama-served locally; the only Ollama path is a cloud
tag, which violates local-first. There is no smaller variant. Excluded.
**Trigger:** Moonshot releases a sub-120 GB variant with Ollama support. No indication this is
planned.

---

## Q4. Multimodal

**Source:** Qwen3.5 model cards and architecture reviews; Qwen3.6-35B-A3B model card
**Finding:** Every Qwen3.5 model is trained on text, images, and video from the start through early
fusion. There is no separate vision variant. Qwen3.5-9B outperforms the previous generation's
Qwen3-VL-30B-A3B on every vision benchmark. Qwen3.6 continues the same architecture. The vision
encoder can be skipped at serve time in some stacks to free KV cache; equivalent Ollama behavior not
confirmed.
**Informs:** Qwen3.5-27B, Qwen3.5-35B-A3B, Qwen3.6-27B and Qwen3.6-35B-A3B are all natively
multimodal. Any of them replaces a separate vision model. One model, both roles.
**Trigger:** confirm Ollama passes image input to these tags; multimodal support is per-model-family.

**Source:** Gemma 4 model cards
**Finding:** Gemma 4 26B-A4B and 31B are natively multimodal.
**Informs:** Gemma 4 26B-A4B is a second natively multimodal MoE candidate.

**Source:** GLM-4.7-Flash documentation
**Finding:** GLM-4.7-Flash is text-only. Multimodal input is not supported. The vision counterpart
is GLM-4.6V-Flash (9B, 128K context, native multimodal function calling, 8 GB GGUF).
**Informs:** GLM-4.7-Flash requires a paired vision model. This is a two-model deployment.

**Source:** gpt-oss model cards
**Finding:** gpt-oss-20b and gpt-oss-120b are text-only, requiring a paired vision model.

---

## Q5. Memory-sycophancy at this scale

**Source:** MemSyco-Bench (Xiang et al., arXiv:2607.01071, v2 July 2026), Appendix E.1
**Finding:** Downstream generators tested: Qwen3-8B, DeepSeek-V4-Flash, Llama-3.3-70B-Instruct,
Llama-3.1-8B-Instruct, GPT-4o mini. Memory construction held fixed so only the generator varied.
Full Dialog average: Qwen3-8B 34.95; DeepSeek-V4-Flash 67.67. Under A-Mem: Qwen3-8B 38.86;
DeepSeek-V4-Flash 71.66. Memory systems improve some settings but gains are unstable: for
Qwen3-8B, A-Mem raised average accuracy while sycophancy on Contextual Scope Control rose from
24.67 to 35.03 and outdated memory use rose from 56.16 to 64.85.
**Informs:** the 8B-to-frontier-MoE gap is roughly 2x (35 to 68).
**Flag:** the same-family Llama-3.3-70B vs Llama-3.1-8B comparison would isolate the scale effect
and was not in the retrieved excerpt. Read Table 3 in full before citing a scale coefficient.
**Trigger:** MemSyco-Bench run on a Spark candidate.

**Source:** SYCON Bench (arXiv:2505.23840, v4 February 2026)
**Finding:** Larger models exhibit reduced sycophancy. Qwen-2.5-72B-Instruct holds its initial
position for 4.90 of 5 turns and flips 0.02 times on average. Qwen-2.5-7B-Instruct holds for 0.83
turns and flips 2.63 times. Reasoning models consistently outperform non-reasoning counterparts.
Instruction tuning increases sycophancy relative to base.
**Informs:** position-holding under pushback improves roughly 6x on turns-held between 7B and 72B in
the same family. Reasoning mode helps. This is the strongest scale-sensitivity evidence for this
specific failure.
**Trigger:** none.

**Source:** The Memory Trust Gap (arXiv:2609.01852, September 2026)
**Finding:** Uses a same-family Qwen3.5-9B/27B pair and studies how retrieval-time override depends
on model capability. In the Benefit suite, models answer with a stale value 0.92-1.00 of the time at
every scale. In the Safety suite, harm below the no-memory baseline under trap conditions is
capability-gated.
**Flag:** the retrieved excerpt truncates mid-sentence at "with the larger models collapsing." The
direction is unresolved and could cut against the simple bigger-is-safer reading. Do not cite the
direction until the full paper is read.
**Trigger:** full read of arXiv:2609.01852.

**Which Spark candidates have published results:** none. The closest same-family data points are
Qwen3-8B (MemSyco), Qwen3.5-9B/27B (Memory Trust Gap), and Qwen-2.5-7B/72B (SYCON).

---

## Q6. Quantization tradeoffs for tool calling

**Source:** Gemma 4 vs Qwen3.5 quantized local coding benchmark (2026)
**Finding:** Gemma 4 26B-A4B: identical results across MXFP4, Q4, Q8. Quant-insensitive.
Qwen3.5-35B-A3B: Q5_K_M has the best compile rate on the hard exam but wobbles on the easy one.
More bits does not monotonically help. Author's hypothesis, stated as speculation: Qwen3.5's
architecture with many small experts may be more sensitive to weight precision loss than Gemma 4's
fewer-expert MoE. gpt-oss-20b: consistent, no draft model needed.
**Informs:** Qwen3.5/3.6 MoE at Q4 may underperform its full-precision benchmark numbers on
tool-calling reliability. The published tau2-Bench scores are full precision. Gemma 4 and gpt-oss
hold their scores under quantization.
**Trigger:** a tool-call eval at Q4_K_M vs Q5_K_M vs NVFP4.

**Source:** 8-model DGX Spark benchmark (February 2026)
**Finding:** Q4_K_M vs Q8_0 across 7 agent task categories: quality difference nearly invisible;
23% speed advantage to Q4_K_M.
**Conflict:** contradicts the Qwen3.5-specific finding above. The February study tested older Qwen3
and coder variants; the later study tested Qwen3.5. The architecture change may be the difference.
**Informs:** Q4_K_M is safe for pre-Qwen3.5 architectures and for Gemma 4 and gpt-oss. Qwen3.5/3.6
needs its own quant test.

**Source:** NVFP4 model cards
**Finding:** NVFP4 is the Blackwell-native format. The published pattern quantizes routed-expert FFN
tensors (97% of parameters) to NVFP4 while keeping attention, vision tower, shared experts, routers,
embeddings and norms in BF16. NVFP4 preserves outlier-sensitive paths by design.
**Informs:** NVFP4 carries the least quality risk on this hardware because it preserves the layers
that matter for tool-call routing. It is also the path Ollama does not fully exploit.
**Trigger:** none.

**Quant recommendations by candidate:**
- Qwen3.6-35B-A3B: NVFP4 if leaving Ollama; Q5_K_M on Ollama. Q4_K_M is the risk case.
- GLM-4.7-Flash: Q4_K_M. No published quant-sensitivity data; conventional-attention MoE, expected
  to behave like the Q4-safe finding.
- gpt-oss-120b: MXFP4, the shipped format. No other quant is meaningful.
- Gemma 4 26B-A4B: MXFP4. Quant-insensitive.
- Qwen3.5-27B: Q4_K_M or NVFP4. Dense; the sensitivity finding was MoE-specific.

---

## Candidate shortlist

Ordered by fit to the stated weighting. Not a decision.

**1. Qwen3.6-35B-A3B**
Fits: yes (Q4 ~22 GB, Q5 28 GB, NVFP4 ~20 GB). Native FC in Ollama: yes, verify per version.
Multi-turn tool score: tau2-Bench Telecom 95.03% full precision; 100/100 Tool-Eval-Bench at NVFP4
via a non-Ollama stack. Multimodal: yes, native. Quant: Q5_K_M on Ollama.
Tradeoff: highest agentic score in class and native vision, but quant-sensitive per one study, and
the Ollama path forfeits MTP. The 95% has not been reproduced at Q4 on Ollama.

**2. GLM-4.7-Flash**
Fits: yes (Q4 19 GB). Native FC in Ollama: yes, verify. Multi-turn tool score: tau2-Bench Telecom
98.83% reasoning mode. Multimodal: no; pair with GLM-4.6V-Flash 9B. Quant: Q4_K_M.
Tradeoff: highest tau2-Bench Telecom score under 100 GB, MIT license, 200K context. Text-only means
a two-model deployment. No DGX Spark tok/s measurement surfaced; architecture predicts the 60-90
tok/s band.

**3. gpt-oss-120b**
Fits: yes (MXFP4 59 GB). Native FC in Ollama: yes, verify. Multi-turn tool score: 100% pass on one
Spark bench, invalid JSON on tool calls per another; conflict unresolved, no tau2-Bench score.
Multimodal: no. Quant: MXFP4 only.
Tradeoff: 40 tok/s on Ollama, the fastest 100B+ option. Tool-calling reliability is disputed across
two Spark benchmarks. Notably verbose.

**4. Gemma 4 26B-A4B**
Fits: yes (MXFP4 15 GB). Native FC in Ollama: yes. Multi-turn tool score: 96.4% pass on one Spark
bench; no tau2-Bench score surfaced. Multimodal: yes, native. Quant: MXFP4, quant-insensitive.
Tradeoff: the safe choice. Native vision, quant-insensitive, 28 tok/s on Ollama, Apache 2.0. Lower
agentic ceiling than Qwen3.6 or GLM per the coding studies, but the most predictable under
quantization.

**5. Qwen3.5-27B (dense)**
Fits: yes (Q4 17 GB). Native FC in Ollama: yes. Multi-turn tool score: tau2-Bench Telecom 93.57%.
Multimodal: yes, native. Quant: Q4_K_M or NVFP4.
Tradeoff: most consistent all-rounder per one guide, highest cloud intelligence index of anything
tested, but 27.6 tok/s dense decode ranks it near-last on wall-time composite. Same speed band as a
32B dense incumbent. Only worth it if quality beats every MoE by enough to justify the latency.

**Excluded:** Kimi (does not fit, no local Ollama path, no small variant). Llama 4 Scout/Maverick
(16-18% tau2-Bench). Older-generation 32B dense (30% tau2-Bench, 9.46 tok/s). DeepSeek distills
(no Spark-class native-FC candidate surfaced; unresolved).

---

## Cross-cutting flags

- Every tau2-Bench score above is full precision on a cloud or vLLM stack. None is reproduced at Q4
  on Ollama on this hardware. A local battery would be the first such measurement.
- The Ollama constraint costs 3-4x decode throughput on the Qwen3.5/3.6 family specifically. The
  constraint is binding for the top candidate.
- The Memory Trust Gap paper may complicate the bigger-reduces-memory-sycophancy reading. Full read
  required before Q5 is closed.
