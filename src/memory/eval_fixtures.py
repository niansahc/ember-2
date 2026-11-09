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

from src.core.config import resolve_vault_path

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


def _caller_vault(path: str | os.PathLike | None) -> Path | None:
    """A vault path a caller holds, resolved the way the caller opens it:
    against the cwd. A script given `--vault vaults/test` reads
    `<cwd>/vaults/test`, so that is the vault the gate must judge."""
    if not path:
        return None
    try:
        return Path(path).resolve()
    except (OSError, ValueError):
        return None


def _configured_test_vault() -> Path | None:
    """VAULT_PATH_TEST by the configured-value rule (project root, not cwd)."""
    try:
        return resolve_vault_path(os.getenv("VAULT_PATH_TEST"))
    except (OSError, ValueError):
        return None


# Set by tools/eval_helpers.require_test_vault to the PRIVATE_VAULT_PATH it
# found before pinning the process to the test vault. After the pin,
# PRIVATE_VAULT_PATH names the test vault, so it can no longer say which
# vault is the live one.
LIVE_VAULT_ENV = "EMBER_LIVE_VAULT_PATH"


def _configured_live_vault() -> Path | None:
    """The live vault: the pre-pin PRIVATE_VAULT_PATH when an eval pinned
    this process (None if it had none), else PRIVATE_VAULT_PATH."""
    if LIVE_VAULT_ENV in os.environ:
        raw = os.environ[LIVE_VAULT_ENV]
    else:
        raw = os.getenv("PRIVATE_VAULT_PATH")
    try:
        return resolve_vault_path(raw)
    except (OSError, ValueError):
        return None


def is_live_vault(vault_path: str | os.PathLike | None) -> bool:
    """True when vault_path resolves to the live vault."""
    target = _caller_vault(vault_path)
    return target is not None and target == _configured_live_vault()


def is_test_vault(vault_path: str | os.PathLike | None) -> bool:
    """True only when vault_path positively resolves to VAULT_PATH_TEST,
    and VAULT_PATH_TEST is not the live vault.

    The second condition catches VAULT_PATH_TEST set to the same path as
    PRIVATE_VAULT_PATH: everything gated on "is the test vault" would then
    write to the live one (tools/eval_helpers.require_test_vault refuses the
    same case).

    Read from the environment on every call rather than cached, because the
    vault can be swapped at runtime and a cached answer would outlive the
    swap.
    """
    target = _caller_vault(vault_path)
    configured = _configured_test_vault()
    if target is None or configured is None:
        return False
    return target == configured and configured != _configured_live_vault()


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
