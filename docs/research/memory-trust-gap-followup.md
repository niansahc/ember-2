# Memory Trust Gap: Single-Paper Follow-Up

**arXiv:2609.01852, Hu & Ramachandran. Preprint, under review at a NeurIPS 2026 workshop.**
**Read in full, September 2026. Findings only.**
**TDD Context:** derived-record provenance and retrieval ranking; relates to the Recalling Too Well mitigations already scoped for this cycle.

Scope: this was a targeted follow-up. An earlier research pass retrieved a truncated excerpt ending
"with the larger models collapsing," cut off before the direction was clear. That sentence bore on
whether moving to a larger local model reduces memory-induced sycophancy. It does not, in one
specific and directly relevant case.

---

## 1. The finding, resolved

Full sentence: in the Safety suite, harm below the no-memory baseline under trap conditions is
capability-gated, with the larger models collapsing most once a stale note is made to look current.

**Direction: larger models collapse harder, not smaller.**

**Trap class: recency.** A stale memory note dated newer than the authoritative current item. Trap
levels L0 through L3 inflate the stale note's apparent recency. Models tested: Qwen3
0.6B / 1.7B / 4B / 8B.

Stale-value reliance across the trap sweep:

| Model | L0 | L1 | L2 | L3 |
|---|---|---|---|---|
| 0.6B | 0.19 | 0.48 | 0.43 | 0.63 |
| 1.7B | 0.00 | 0.43 | 0.45 | 0.72 |
| 4B | 0.00 | 0.09 | 0.83 | 0.93 |
| 8B | 0.00 | 0.02 | 0.94 | 1.00 |

The size ordering flips between L1 and L2. At L0 and L1, larger models resist the stale note
better. At L2 and L3, once the stale note is dated newer, the 8B goes to full reliance (1.00) and
loses all of its accuracy. The 0.6B tops out at 0.63.

**Mechanism:** a date-parsing probe confirms the larger models read timestamps correctly, and
higher parsing accuracy is associated with greater reliance on the stale note. The larger models
are fooled because they can read the dates. The smaller models are protected by not being able to.

The recency effect is a step, not a slope. Reliance jumps the instant the stale note is dated at
least one day newer than the current item, then saturates.

Cross-size contrast: recency 8B minus 0.6B = +0.302, CI [.269, .336], interaction supported.
Replicated on Llama-3.2-1B/3B and Llama-3.1-8B with the same sign on capable sizes.

---

## 2. Relationship to the scale-reduces-sycophancy finding

Partially contradicts it, and the paper says so. Prior work (De Marez et al. 2026) finds larger
instruction-tuned models more robust to overt sycophantic flips, the opposite direction.
ConflictBank (Su et al. 2024) already reported larger models can be more susceptible to conflicting
evidence, so a reversal is not unprecedented.

**The exception class, precisely:** stale stored evidence carrying a timestamp newer than the
correct authoritative evidence. Four features were tested in a 2x2x2x2 factorial. Only one produces
the scale reversal:

| Feature | Scale behaviour |
|---|---|
| label removed | universal, no scale trend (all sizes fooled equally, +0.23 to +0.39) |
| recency (stale dated newer) | larger fooled harder (+0.26 at 0.6B, +0.56 at 8B) |
| authority (inflated source) | weak, scale-flat (+0.11 to +0.14) |
| position (stale first) | sign flips: fools 0.6B, protects 4B/8B; Qwen-specific, did not replicate on Llama |

**How narrow:** one feature of four, requiring a parseable timestamp and a stale item dated at least
one day newer. The general finding holds elsewhere. In the explicit-conflict condition (stale and
correct both stored, no recency manipulation), small models follow the stale value 0.50 of the time
and the 4B/8B models 0.01 and 0.00. Larger models are strictly better at adjudicating conflicting
stored memory when timestamps are honest.

The paper's own framing: capability is not uniformly protective.

---

## 3. Correction to the prior pass

The Qwen3.5-9B/27B same-family pair is **not** run in this paper. It appears in Related Work as a
citation to STALE (Chao et al. 2026), a prior benchmark with 400 scenarios where the best model
reaches 55.2%. This paper's own series is Qwen3 0.6/1.7/4/8B plus a Llama replication. No 9B-to-27B
comparison is reported anywhere in it. STALE would need a separate read for that comparison.

---

## 4. Benefit suite vs Safety suite

**Benefit suite:** the task is unsolvable without the stored fact; no current context is present.
No-memory baseline floored at chance (0.33). Stale reliance is 0.92 / 0.99 / 1.00 / 1.00 across
sizes. Harm is -0.33 to -0.37 at every size. Over-trust is universal here, not capability-gated.

**Safety suite:** an authoritative tool always holds the correct value. No-memory baseline ceilinged
at 0.98 or above. Any net negative is real harm: the model followed a stale memory over an
authoritative source it had in context.

**"Capability-gated"** means the harm only appears once a model is accurate enough for the stale
value to cost it something. A small model has little accuracy to lose. A large model that reads the
recency cue correctly and defers to it loses everything. Reliance can stay near 1.0 across sizes
while net harm differs, because net harm is reliance multiplied by the accuracy that reliance
displaces.

**Not memory-specific.** A stale document is trusted as much as or more than a stale memory at three
of four sizes (not significant at 8B). Web search results and ingested content carrying newer dates
than a stored correction fall in the same class.

**Mitigations tested (accuracy gain over a raw frame):**

| Intervention | 0.6B | 1.7B | 4B | 8B |
|---|---|---|---|---|
| metadata (timestamp + source per item) | +0.30 | +0.28 | +0.53 | +0.54 |
| oracle (stale item removed before prompt) | +0.46 | +0.57 | +0.63 | +0.59 |

Structural marking of the correct item as authoritative: +0.42 (4B), +0.33 (8B), +0.06 (0.6B),
+0.00 (1.7B). A verbal instruction to prefer the newest authoritative source has little effect and
increases reliance in some settings. Thinking mode does not close the gap at 8B and increases
over-trust at 0.6B.

---

## 5. Implication for local model tiers

The paper's data stop at 8B. No 30B-class model is tested. Extrapolation above 8B is outside the
evidence.

Within the tested range the direction is unambiguous: for recency-inflated stale evidence, moving
from 4B to 8B made the collapse worse (0.83 to 0.94 at L2; 0.93 to 1.00 at L3). For every other trap
class, and for explicit-conflict adjudication, moving up helped. The Llama series shows the same
pattern on its capable sizes.

What the paper supports: a more capable model handles honest timestamps better and dishonest
timestamps worse. For a memory-augmented assistant, the determining factor is whether the pipeline
can produce a stale record dated newer than its own correction. If it can, scale amplifies that
specific harm. If it cannot, scale is protective on the remaining trap classes.

What the paper does not support: any claim about 30B specifically, or whether the recency reversal
continues, plateaus, or reverses again above 8B.

---

## 6. Architectural relevance

**Raw append-only storage is not the exposure.** A raw stale record is written at its own time and
is therefore dated older than a later raw correction. That is the honest-timestamp case, where
larger models do better.

**Derived records are the exposure.** Any layer that writes a new record, with a new timestamp, from
older content constructs the trap directly: a reflection that compresses an old preference into a
record dated today is a stale note made to look current. Where retrieval ranking also applies
temporal decay, the derived record then outranks the older raw correction it contradicts.

This is the same defect surfaced independently by the memory-sycophancy literature on lossy
extraction (arXiv:2606.10949), reached from a different direction. The two findings agree on the
mitigation: preserve provenance on derived records, and rank derived content below the source it was
derived from.

Consequence for model selection: the derived-timestamp problem should be fixed before, not after,
moving to a more capable model, because this paper's evidence is that capability amplifies that
specific failure rather than reducing it.
