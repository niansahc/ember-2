"""Per-term retrieval trace capture and offline replay.

Phase 1 of the measurement-first audit: record what the scoring pipeline
did, at term granularity, so sensitivity analysis can be run offline
against a parameterized model instead of by repeatedly measuring the live
system.

Capture is read-only. Traces are written outside the repository.
"""
