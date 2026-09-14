"""
tools/retrieval_ablation/

Retrieval-architecture ablation eval: does Ember's memory-type taxonomy and
hot/warm/cold tiering measurably add over a naive verbatim + embedding baseline?

The open question is recorded in docs/Ember2_TDD.md:2170 (MemPalace watch item),
:1999 (research entry) and :1797 (the v0.19.0 task, which requires a measurement
plan before the ablation starts -- graded nDCG here is that plan, replacing the
one-verdict-change floor of the existing 15-query harness with a continuous
metric).

Entry point is `python tools/eval_retrieval.py --ablation`.
"""
