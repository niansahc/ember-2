"""
tests/eval/seeder.py

Synthetic-corpus seeder for the grounding-fidelity eval.

Writes a small, known, entirely synthetic corpus of memory records into the
active vault via the normal write path (src/memory/write_memory.py), so that a
grounding query run through the live Ember pipeline exercises REAL retrieval over
a corpus whose ground-truth facts we know. That is the whole point: grounding
fidelity must test whether retrieval surfaced the right records and the response
stayed faithful to them - not whether the model is faithful to an injected
context string.

Vault safety: all content here is synthetic (generic project/schedule facts, no
real names, no real vault content). In-process unit tests run under the conftest
`isolate_to_test_vault` override; the live eval calls
tools/eval_helpers.py::swap_to_test_vault first so the corpus lands in the
configured test vault, never the real one.
"""

from __future__ import annotations

from src.memory.write_memory import write_memory


# Each record carries its ground-truth `fact` - the single claim the grounding
# judge checks a response against. Text is deliberately long enough to survive
# should_skip_memory. Entirely synthetic.
SEED_CORPUS = [
    {
        "text": (
            "Project Atlas is the internal name for the Q3 storage-migration "
            "effort. Its hard deadline is Friday, October 3rd."
        ),
        "fact": "The Project Atlas deadline is Friday, October 3rd.",
        "query": "When is the Project Atlas deadline?",
        "memory_type": "journal",
    },
    {
        "text": (
            "The weekly platform-team sync is held every Tuesday at 10am in the "
            "smaller upstairs meeting room, not the main hall."
        ),
        "fact": "The platform-team sync is Tuesdays at 10am.",
        "query": "When is the weekly platform-team sync?",
        "memory_type": "journal",
    },
    {
        "text": (
            "The reading group is currently working through a book on distributed "
            "systems; the chosen title for this month is a text on consensus "
            "algorithms."
        ),
        "fact": "The reading group is reading a book about consensus algorithms.",
        "query": "What is the reading group reading this month?",
        "memory_type": "journal",
    },
]


def seed_corpus(corpus: list[dict] | None = None) -> list[dict]:
    """Write the synthetic corpus into the active (test) vault.

    Returns the corpus records (including their `fact` and `query`) so the
    grounding eval can drive each query and judge the response against the known
    fact. Idempotency is not required: the vault is throwaway per eval run.
    """
    corpus = corpus if corpus is not None else SEED_CORPUS
    written = []
    for rec in corpus:
        write_memory(
            text=rec["text"],
            memory_type=rec.get("memory_type", "journal"),
            source="eval_seed",
            tags=["eval", "synthetic"],
            metadata={"eval_seed": True},
        )
        written.append(rec)
    return written
