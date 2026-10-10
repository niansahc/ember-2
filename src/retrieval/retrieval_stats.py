"""
src/retrieval/retrieval_stats.py

Process-level suppression of retrieval-stat writes.

`ContextService.build_context(read_only=True)` (issue #206) is a per-call
opt-in, and a per-call opt-in is only as good as the caller. Every
investigative caller in the repo has to remember it, and the ablation
harness did not: it built packets on the read path and relied on snapshot
and restore of memory.db around each arm to undo the damage afterwards.
That works until a harness forgets to restore, or reads the database
mid-run, or runs against a vault it does not own.

This module is the second layer. It gates the write at the point the write
actually happens -- `SqliteVectorStore.update_retrieval_stats` -- so any
entry point is covered, including ones that never go through
`build_context` at all. Two ways to arm it:

    with retrieval_stats_disabled():
        ...                              # in-process, nestable

    EMBER_RETRIEVAL_STATS_READ_ONLY=1    # whole process, e.g. a subprocess
                                         # spawned per replay arm

Why a suppression switch rather than making the write opt-in: a chat turn
that silently stopped recording deliveries would look exactly like a
correct read-only run, and tiering's only upward path would quietly die
(ADR-015). Delivery must stay the default; not-delivery must be declared.

The environment variable is read on every call, never cached, so a test or
a harness can set and clear it without a module reload.
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

ENV_FLAG = "EMBER_RETRIEVAL_STATS_READ_ONLY"

_TRUTHY = {"1", "true", "yes", "on"}

# Nesting depth, not a boolean: nested suppression scopes must not let the
# inner one re-enable writes on exit.
_local = threading.local()


def _depth() -> int:
    return getattr(_local, "depth", 0)


def retrieval_stats_disabled_now() -> bool:
    """True when retrieval-stat writes are currently suppressed."""
    if _depth() > 0:
        return True
    return os.environ.get(ENV_FLAG, "").strip().lower() in _TRUTHY


@contextmanager
def retrieval_stats_disabled():
    """Suppress retrieval-stat writes for the duration of the block.

    Thread-local and nestable. Use it around any replay, ablation, eval or
    diagnostic run that assembles context packets.
    """
    _local.depth = _depth() + 1
    try:
        yield
    finally:
        _local.depth = max(0, _depth() - 1)
