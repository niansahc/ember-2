"""
tests/eval/test_seeder.py

Unit tests for the grounding-corpus seeder. Runs under the session-scoped
`isolate_to_test_vault` conftest fixture, so write_memory lands in a throwaway
temp vault - never the real vault. The corpus is entirely synthetic.
"""

from tests.eval.seeder import seed_corpus, SEED_CORPUS
from src.memory.read_memory import read_memories


def test_corpus_is_all_synthetic_and_substantive():
    # Every record is generic synthetic text (no real vault content) and long
    # enough that should_skip_memory will not drop it.
    assert SEED_CORPUS
    for rec in SEED_CORPUS:
        assert len(rec["text"]) >= 40


def test_seed_corpus_writes_and_is_retrievable():
    written = seed_corpus()
    assert len(written) == len(SEED_CORPUS)
    recs = read_memories(memory_type="journal", limit=500)
    corpus_text = " ".join(r.get("text", "") for r in recs)
    for rec in SEED_CORPUS:
        assert rec["text"] in corpus_text


def test_seed_corpus_returns_ground_truth_facts():
    # Each seeded record exposes the ground-truth fact the grounding judge checks
    # response claims against.
    written = seed_corpus()
    for rec in written:
        assert "fact" in rec and rec["fact"]
