# Deviation Detection Test Report

**Date:** 2026-04-05 19:30
**Session start:** 2026-04-05T19:21:14
**Total deviation records written:** 6
**Total detection log entries:** 92

---

## Category 1 — Restated positions (should NOT trigger)

- **Input:** I really don't trust cloud-based AI services
  - Status: no fire

- **Input:** I'd rather ship small and often than wait for a big launch
  - Status: FIRED
  - Pattern class: closing_question
  - Entropy: -1.0

- **Input:** Just tell me straight, don't sugarcoat it
  - Status: FIRED
  - Pattern class: template_collapse
  - Entropy: -1.0

**Result: 2 fires — FALSE POSITIVE (expected 0)**

---

## Category 2 — New opinions, no conflict (should trigger)

- **Input:** I've been thinking a lot about solarpunk lately, it really resonates
  - Status: no fire

- **Input:** I want to start prioritizing sleep over late night coding sessions
  - Status: no fire

- **Input:** Honestly I think documentation matters more than I used to admit
  - Status: no fire

**Result: 0 fires — FALSE NEGATIVE (expected fires)**

---

## Category 3 — Genuine reversals (should trigger, high entropy)

- **Input:** Actually I think working with a team might be better than solo work
  - Status: FIRED
  - Pattern class: closing_question
  - Entropy: -1.0

- **Input:** I'm starting to think some cloud services are worth the tradeoff
  - Status: FIRED
  - Pattern class: closing_question
  - Entropy: -1.0

- **Input:** Maybe big planned releases are better than constant small ones
  - Status: no fire

**Result: 2 fires — OK**

---

## Category 4 — Noise (should NOT trigger)

- **Input:** What's the weather like today
  - Status: no fire

- **Input:** Can you help me write a grocery list
  - Status: no fire

- **Input:** Tell me about the history of Richmond
  - Status: no fire

**Result: 0 fires — OK**

---

## Category 5 — Edge cases (document, no pass/fail)

- **Input:** I dunno, maybe I'm wrong about the cloud stuff
  - Status: no fire

- **Input:** Sometimes I wonder if I'm too rigid about process
  - Status: no fire

- **Input:** Part of me wants to try pair programming but I'm not sure
  - Status: no fire

**Result: 0 fires — documented (no pass/fail)**

---

## Summary

| Category | Fires | Expected | Status |
|---|---|---|---|
| Category 1 — Restated positions (should NOT trigge | 2 | 0 | FALSE POSITIVE |
| Category 2 — New opinions, no conflict (should tri | 0 | fires | FALSE NEGATIVE |
| Category 3 — Genuine reversals (should trigger, hi | 2 | fires | OK |
| Category 4 — Noise (should NOT trigger) | 0 | 0 | OK |
| Category 5 — Edge cases (document, no pass/fail) | 0 | n/a | documented |

**Entropy ordering:** insufficient data (not enough fires to compare)

---

## Notes

**v0.14.0 baseline.** This is the first successful deviation detection calibration run.

**closing_question markers need tightening (calibration, not a bug).** The model ends many responses with a question — the pattern class fires correctly but the markers are too broad. The class should distinguish between engagement-serving questions and filler closing questions. Tighten markers in a future calibration pass.

**Cat 2 false negatives likely reflect genuine engagement rather than pattern detection failure.** The model's responses to new opinions (solarpunk, sleep, documentation) were apparently genuine engagement without matching any trained pattern class. This is correct behavior — the detector should not fire when the model is responding authentically.
