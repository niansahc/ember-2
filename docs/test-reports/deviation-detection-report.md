# Deviation Detection Test Report

**Date:** 2026-04-05 14:52
**Session start:** 2026-04-05T14-47-32
**Total deviation records written:** 0
**Total detection log entries:** 8

---

## Category 1 — Restated positions (should NOT trigger)

- **Input:** I really don't trust cloud-based AI services
  - Status: no fire

- **Input:** I'd rather ship small and often than wait for a big launch
  - Status: no fire

- **Input:** Just tell me straight, don't sugarcoat it
  - Status: no fire

**Result: 0 fires — OK**

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
  - Status: no fire

- **Input:** I'm starting to think some cloud services are worth the tradeoff
  - Status: no fire

- **Input:** Maybe big planned releases are better than constant small ones
  - Status: no fire

**Result: 0 fires — FALSE NEGATIVE (expected fires)**

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
| Category 1 — Restated positions (should NOT trigge | 0 | 0 | OK |
| Category 2 — New opinions, no conflict (should tri | 0 | fires | FALSE NEGATIVE |
| Category 3 — Genuine reversals (should trigger, hi | 0 | fires | FALSE NEGATIVE |
| Category 4 — Noise (should NOT trigger) | 0 | 0 | OK |
| Category 5 — Edge cases (document, no pass/fail) | 0 | n/a | documented |

**Entropy ordering:** insufficient data (not enough fires to compare)
