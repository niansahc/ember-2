"""
src/memory/eval_fixtures.py

One place that answers: is this record a test fixture, and may it be indexed
into the vault this process is pointed at.

Why this exists
---------------
`tests/eval/seeder.py` writes a synthetic corpus through the ordinary
`write_memory` path so the grounding eval exercises real retrieval. Its
docstring assumes a throwaway vault; nothing enforced that. Twelve of those
fixtures were found in the live personal vault's index (#211), and after the
rebuild they carry real 768-dimension embeddings, so they went from silently
unreachable to genuinely retrievable in someone's personal memory.

The canonical JSON files are not the problem and are not touched -- the
append-only rule owns those, and a vault record that exists is a fact about
the vault. The problem is indexing them, because the index is what retrieval
reads.

Fail closed
-----------
`may_index_eval_fixture` returns False unless the destination vault is
positively identified as the configured test vault. An unset or
unresolvable `VAULT_PATH_TEST`, a path that will not resolve, a vault that
is not the test vault: all of those mean "do not index".

That direction is deliberate. Getting it wrong one way puts synthetic
records in someone's personal memory, silently, where they surface as
retrieved context and are indistinguishable from real recollection. Getting
it wrong the other way makes an eval visibly fail to find its own corpus,
which is a configuration error announcing itself. The second is recoverable
in a minute; the first was not noticed for months.
"""

from __future__ import annotations

import os
from pathlib import Path

# Marks a record as belonging to an eval corpus rather than to the user.
# `source` is the primary signal; the metadata flag is a secondary one that
# tests/eval/seeder.py also sets, kept so a record missing one is still caught.
EVAL_SEED_SOURCE = "eval_seed"
EVAL_SEED_METADATA_KEY = "eval_seed"


def is_eval_fixture(source: str | None, metadata: dict | None = None) -> bool:
    """True when a record is eval-corpus material rather than user memory."""
    if (source or "") == EVAL_SEED_SOURCE:
        return True
    return bool((metadata or {}).get(EVAL_SEED_METADATA_KEY))


def _resolved(path: str | os.PathLike | None) -> Path | None:
    if not path:
        return None
    try:
        return Path(path).expanduser().resolve()
    except (OSError, RuntimeError, ValueError):
        return None


def is_test_vault(vault_path: str | os.PathLike | None) -> bool:
    """True only when vault_path positively resolves to VAULT_PATH_TEST.

    Read from the environment on every call rather than cached, because the
    vault can be swapped at runtime and a cached answer would outlive the
    swap.
    """
    target = _resolved(vault_path)
    configured = _resolved(os.getenv("VAULT_PATH_TEST"))
    if target is None or configured is None:
        return False
    return target == configured


def may_index_eval_fixture(vault_path: str | os.PathLike | None) -> bool:
    """Whether an eval fixture may be written into this vault's index."""
    return is_test_vault(vault_path)


def should_index_record(
    vault_path: str | os.PathLike | None,
    source: str | None,
    metadata: dict | None = None,
) -> bool:
    """The single question the write and rebuild paths both ask.

    Everything that is not an eval fixture indexes normally; an eval fixture
    indexes only into the configured test vault.
    """
    if not is_eval_fixture(source, metadata):
        return True
    return may_index_eval_fixture(vault_path)
