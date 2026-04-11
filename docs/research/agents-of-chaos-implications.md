# Agents of Chaos — Architectural Implications for Ember-2

**Source:** Shapira et al., arXiv:2603.20021, February 2026. 38 researchers (Northeastern/Harvard/MIT/Stanford/CMU). Two-week live red-team experiment with six autonomous AI agents.

**Date:** 2026-04-10

---

## Key finding relevant to Ember

Locally rational choices compounding into problematic overall patterns without external reference points. The paper documents this as a multi-agent phenomenon (agents negotiated reasonable individual policies that produced catastrophic collective outcomes), but the **single-agent analog** applies directly to Ember:

Internal self-evaluation confirming locally rational behavior without external correction. Each individual response passes Ember's constitutional review. Each deviation record looks value-aligned against the nature document. But the accumulated pattern may drift from intended character without anyone noticing — because the system evaluating drift is the same system that may be drifting.

---

## Implications filed

### 1. Deviation engine drift risk

The deviation engine records value-aligned deviations, but the values are evaluated by the same system that may be drifting. If the model's behavior shifts gradually, the deviation detector (which uses the same model for second-pass classification) will classify the shifted behavior as normal.

**Mitigation:** Periodic comparison of deviation record distribution against the nature document. Added to the release gate checklist (CLAUDE.md pre-release section):

> Deviation drift check — review deviation record distribution against nature document; verify accumulated character is consistent with intended character.

This is a human-in-the-loop check. It cannot be automated by the same system it is checking.

### 2. Self-evaluation loops (v0.16.0) must evaluate against external ground truth

When self-evaluation and decision-memory loops are implemented (v0.16.0 roadmap), the evaluation criteria must come from externally stable documents (constitution.yaml, nature.yaml), NOT from Ember's accumulated behavior patterns.

If the evaluation loop asks "is this response consistent with how I've been responding?", the answer will always be yes — even if the overall pattern has drifted. The correct question is "is this response consistent with the constitution and nature document?" — which are human-authored, versioned, and externally reviewable.

### 3. Asymmetric error tolerance for self-evaluation

When uncertain whether a pattern is problematic, flag for human review. Conservative by default.

False negatives in safety evaluation ("missed a real problem") are cheaper than false positives ("flagged something that was fine") **only in non-safety contexts**. For self-evaluation of behavioral drift, the asymmetry reverses: missing real drift is expensive (the system becomes something it shouldn't be), flagging a false alarm is cheap (a human reviews and clears it).

Default: when uncertain, flag. The manual evaluation battery is the clearing mechanism.

### 4. Human-in-the-loop check must happen on a schedule

The paper documents that the agents' negotiated safety policies were reasonable in isolation but produced problems that only became visible at the system level over time. The same applies to Ember's accumulated behavior — it must be periodically reviewed at a level above individual responses.

The mechanisms for this already exist:
- Manual evaluation battery (docs/eval_manual_test_battery.md) — tests behavioral quality across 7 categories
- Deviation drift check (release gate) — compares deviation distribution against nature
- Conversation quality eval (tools/eval_conversations.py) — external evaluator scores responses

What doesn't exist yet: a **schedule**. These checks run at release time, which is human-initiated and irregular. A periodic cadence (weekly or monthly) independent of release timing would close the gap the paper identifies.

### 5. Local-first without explicit external ground truth replicates the isolation failure mode

The paper's agents failed because they had local rationality but no external reference frame. Ember is local-first by design — no cloud evaluation service, no external benchmark, no peer system to compare against.

This isolation is a feature for privacy but a risk for drift. The mitigation is explicit: Ember's external ground truth is:
- `config/constitution.yaml` — human-authored governance rules
- `config/nature.yaml` — human-authored character document
- The manual evaluation battery — human-administered behavioral checks
- The release checklist — human-approved before each version ships

These are the external reference points. Periodic review (not just release-gated review) closes the loop.

---

## Does not apply to Ember

- **Collective instability** — multi-agent only. Ember is a single agent with no peer coordination.
- **Peer signal mechanism** — the paper's agents influenced each other's policies. Ember has no peer to negotiate with. (This changes if agentic orchestration ships in v0.16.0 — revisit then.)

---

## References

- Manual evaluation battery: docs/eval_manual_test_battery.md
- Release checklist: CLAUDE.md (pre-release section)
- Deviation engine: ADR-013, ADR-026
- Self-evaluation loops: v0.16.0 roadmap
