"""
src/observability/guard_counters.py

Hit counters on the guards, thresholds, floors, exclusions and filters in
the retrieval and scoring path.

The question these answer is one static analysis cannot: which guards
execute, evaluate their predicate, and are never true on real data. A guard
that is never reached and a guard that is reached ten thousand times and
never fires look identical in coverage tooling and in the source, and they
are completely different findings -- the first is dead code, the second is
a rule about a situation this corpus does not contain. So every site
records BOTH numbers, and a site with evaluations > 0 and firings == 0 is
the interesting one.

Three rules govern this module, in order of importance.

1. IT CANNOT CHANGE SCORING. Every counting call returns the value it was
   given, so an instrumented predicate is the same expression with a
   recording side effect. The recorder itself is wrapped so that a failure
   anywhere in it is swallowed: a broken counter must degrade to no
   counting, never to a broken turn.

2. IT COUNTS LIVE TRAFFIC ONLY. Test fixtures exercise guards production
   never does -- a suite that deliberately constructs a third-party
   authorship record would report that exclusion as live when no real
   record has ever had that tag. Recording is therefore off unless a
   recording scope is open, build_context opens one only for a real turn
   (not read_only, not inside retrieval_stats_disabled), and the scope
   refuses to open under pytest at all.

3. IT DOES NOT TOUCH THE VAULT. Counts are site names and integers, no
   vault content of any kind, and they are written outside the vault. The
   writer refuses a path inside it.

Counts accumulate in memory during a turn and are flushed once at the end
of the scope, so the hot path costs a dict increment rather than a write.
They persist in SQLite and accumulate across restarts, and the file is
readable with a read-only connection while the API is serving.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / "logs" / "observability" / "guard_counters.db"

ENV_DB_PATH = "EMBER_GUARD_COUNTER_DB"
ENV_DISABLE = "EMBER_GUARD_COUNTERS_OFF"

KIND_PREDICATE = "predicate"
KIND_PARENT = "parent"
KIND_BRANCH = "branch"

# Per-thread pending counts. Thread-local rather than shared so the hot
# path needs no lock; the merge into the shared store takes one, once, at
# the end of the scope.
_local = threading.local()
_write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

def _under_pytest() -> bool:
    """True when this process is running the test suite.

    Checked at scope-open rather than at import: a test that spins up the
    app in-process would otherwise inherit whatever the import-time answer
    happened to be. Test fixtures are the wrong population for this
    measurement -- they exercise guards on purpose -- so they are excluded
    structurally rather than by remembering to disable something.
    """
    return "pytest" in sys.modules or "PYTEST_CURRENT_TEST" in os.environ


def recording_enabled() -> bool:
    return getattr(_local, "pending", None) is not None


@contextmanager
def recording(enabled: bool = True):
    """Open a recording scope for one live turn.

    Nested scopes do not nest counts: the inner one is a no-op, so a
    caller that wraps another cannot double count.
    """
    if (
        not enabled
        or recording_enabled()
        or _under_pytest()
        or os.environ.get(ENV_DISABLE, "").strip().lower() in {"1", "true", "yes", "on"}
    ):
        yield False
        return

    _local.pending = {}
    try:
        yield True
    finally:
        pending = _local.pending
        _local.pending = None
        try:
            if pending:
                flush(pending)
        except Exception:  # noqa: BLE001
            # A counter that cannot be written must not fail the turn it
            # was measuring. This is instrumentation, not a feature.
            logger.debug("[GUARDS] counter flush failed", exc_info=True)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def _bump(site: str, kind: str, parent: str | None, evaluations: int, firings: int) -> None:
    pending = getattr(_local, "pending", None)
    if pending is None:
        return
    entry = pending.get(site)
    if entry is None:
        pending[site] = [kind, parent, evaluations, firings]
    else:
        entry[2] += evaluations
        entry[3] += firings


def count(site: str, fired: bool) -> bool:
    """Record one evaluation of `site`, and a firing when `fired`.

    Returns `fired` unchanged, so `if count("x", a > b):` is the same
    expression as `if a > b:` with a side effect. That passthrough is what
    makes the instrumentation provably behaviour-preserving rather than
    carefully behaviour-preserving.
    """
    _bump(site, KIND_PREDICATE, None, 1, 1 if fired else 0)
    return fired


def branch(site: str, which: str) -> str:
    """Record which arm of a multi-way branch was taken.

    The parent row carries the evaluation count; each arm carries its own
    firings. An arm that never appears has never been taken, and the
    parent's evaluations say how many chances it had.
    """
    _bump(site, KIND_PARENT, None, 1, 0)
    _bump(f"{site}={which}", KIND_BRANCH, site, 0, 1)
    return which


def reached(site: str) -> None:
    """Record that a site executed, with no predicate to evaluate."""
    _bump(site, KIND_PREDICATE, None, 1, 0)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def database_path() -> Path:
    configured = os.environ.get(ENV_DB_PATH)
    return Path(configured) if configured else DEFAULT_DB_PATH


def _assert_outside_vault(path: Path) -> None:
    """Counters are operational data and must not land in the vault.

    The vault is append-only canonical memory; a counter file inside it
    would be indexed, backed up and rebuilt as though it were a record.
    """
    try:
        from src.core.config import get_private_vault_path

        vault = get_private_vault_path().resolve()
    except Exception:  # noqa: BLE001 -- no vault configured is fine
        return
    resolved = path.resolve()
    if resolved == vault or vault in resolved.parents:
        raise ValueError(
            f"refusing to write guard counters inside the vault ({resolved}). "
            "Counters are operational data, not memory."
        )


_connections: dict[str, sqlite3.Connection] = {}


def _connect(path: Path) -> sqlite3.Connection:
    """One cached connection per database, in WAL mode.

    Both parts are about cost. Opening a connection and running CREATE
    TABLE IF NOT EXISTS on every turn measured at roughly 29ms per turn on
    a warm cache, which is not a price instrumentation gets to charge the
    request path. Caching the connection and taking WAL with
    synchronous=NORMAL brings that to single digits.

    WAL also does something the measurement needs: a reader gets a
    consistent snapshot without blocking the writer, so `tools/
    guard_counters.py` can be run against a live API rather than requiring
    a stop.
    """
    key = str(path.resolve())
    cached = _connections.get(key)
    if cached is not None:
        return cached

    _assert_outside_vault(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    # NORMAL rather than FULL: a counter lost to an unclean shutdown costs
    # a few hits off a total in the thousands, which is not worth an fsync
    # on every turn.
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guard_counters (
            site        TEXT PRIMARY KEY,
            kind        TEXT NOT NULL,
            parent      TEXT,
            evaluations INTEGER NOT NULL DEFAULT 0,
            firings     INTEGER NOT NULL DEFAULT 0,
            first_seen  TEXT,
            last_seen   TEXT
        )
        """
    )
    conn.commit()
    _connections[key] = conn
    return conn


def close_connections() -> None:
    """Drop cached connections. For tests and for a clean shutdown."""
    with _write_lock:
        for conn in _connections.values():
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        _connections.clear()


def flush(pending: dict, path: Path | None = None) -> None:
    """Merge one scope's counts into the database. Accumulative."""
    if not pending:
        return
    now = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    target = path or database_path()

    with _write_lock:
        conn = _connect(target)
        try:
            conn.executemany(
                """
                INSERT INTO guard_counters
                    (site, kind, parent, evaluations, firings, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(site) DO UPDATE SET
                    evaluations = evaluations + excluded.evaluations,
                    firings     = firings + excluded.firings,
                    last_seen   = excluded.last_seen
                """,
                [
                    (site, kind, parent, evaluations, firings, now, now)
                    for site, (kind, parent, evaluations, firings) in pending.items()
                ],
            )
            conn.commit()
        except sqlite3.Error:
            # A cached connection can go stale if the file is replaced
            # underneath it (a reset from another process, a moved vault).
            # Drop it so the next flush reopens rather than failing forever.
            _connections.pop(str(target.resolve()), None)
            raise


def read_all(path: Path | None = None) -> list[dict]:
    """Every counter, readable while the API is serving.

    Opens read-only so a reader can never block or corrupt a writer.
    """
    target = path or database_path()
    if not target.exists():
        return []
    conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute("SELECT * FROM guard_counters")]
    finally:
        conn.close()

    by_site = {row["site"]: row for row in rows}
    for row in rows:
        # A branch arm's denominator is its parent's evaluation count.
        parent = by_site.get(row["parent"]) if row["parent"] else None
        row["denominator"] = parent["evaluations"] if parent else row["evaluations"]
    return sorted(rows, key=lambda row: row["site"])


def reset(path: Path | None = None) -> None:
    """Clear every counter. Used to start a fresh traffic window."""
    target = path or database_path()
    with _write_lock:
        conn = _connect(target)
        conn.execute("DELETE FROM guard_counters")
        conn.commit()
