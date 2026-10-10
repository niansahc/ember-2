"""
tests/test_unique_timestamp.py

Concurrent id generation must never return the same timestamp twice
(ultrareview #280, item 1).

Every timestamp-named record writer used to guard against same-tick
collisions with an unlocked check-then-set:

    candidate = datetime.now().strftime(fmt)
    if candidate != _last:
        _last = candidate
        return candidate

Two threads can both pass the check before either assigns, and both
return the same value. Records are named `{timestamp}.json`, so the
second write overwrites or is dropped. project._now_id() and the two
lodestone sites had no guard at all.

The race harness is deterministic, not timing-dependent:
  - the module's `datetime` is replaced by a fake clock that advances one
    step every READS_PER_TICK reads, so concurrent readers see the same
    value, and
  - the value's strftime() returns a str whose __ne__ sleeps before
    comparing, which releases the GIL between the check and the set. The
    right-hand operand is bound before the sleep, so a second thread that
    compares during the sleep still sees the stale `_last`.

Without a lock that held across read-compare-set, collisions are certain.
With one, the other threads block on the lock and every value is unique.

The positive control runs the same harness against an unlocked
check-then-set generator defined here and asserts it does collide, so the
zero-duplicate assertions cannot pass vacuously.
"""

from __future__ import annotations

import itertools
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

import src.core.timestamps as timestamps_mod
import src.memory.lodestone_service as lodestone_service
import src.memory.project as project
import src.memory.session as session
import src.memory.write_memory as write_memory
import src.state.state_service as state_service
import src.state.timer_service as timer_service
import src.tasks.task_service as task_service

READS_PER_TICK = 4
THREADS = 8
PER_THREAD = 10

US_FORMAT = "%Y-%m-%dT%H-%M-%S-%f"
SECOND_FORMAT = "%Y-%m-%dT%H-%M-%S"


class _YieldingStr(str):
    """A str whose inequality check yields the GIL before comparing."""

    __hash__ = str.__hash__

    def __ne__(self, other):
        time.sleep(0.001)
        return str.__ne__(self, other)


class _FakeDateTime(datetime):
    """A datetime whose strftime result yields inside comparisons."""

    def strftime(self, fmt):
        return _YieldingStr(super().strftime(fmt))


class _CoarseClock:
    """Stands in for the `datetime` class in a patched module.

    now() advances by `step` once every READS_PER_TICK reads. itertools.count
    is thread-safe under the GIL, so the tick sequence is exact. Every other
    attribute delegates to the real datetime class.
    """

    def __init__(self, step: timedelta) -> None:
        self._reads = itertools.count()
        self._step = step
        self._base = datetime(2026, 1, 1, 12, 0, 0)

    def now(self, tz=None):
        n = next(self._reads)
        value = self._base + self._step * (n // READS_PER_TICK)
        return _FakeDateTime(
            value.year, value.month, value.day,
            value.hour, value.minute, value.second, value.microsecond,
            tzinfo=tz,
        )

    def __getattr__(self, name):
        return getattr(datetime, name)


def _race(fn, threads: int = THREADS, per_thread: int = PER_THREAD) -> list[str]:
    """Call fn per_thread times on each of `threads` threads, started together."""
    barrier = threading.Barrier(threads)
    results: list[str] = []
    results_lock = threading.Lock()
    errors: list[BaseException] = []

    def worker():
        try:
            barrier.wait()
            local = [fn() for _ in range(per_thread)]
            with results_lock:
                results.extend(local)
        except BaseException as exc:  # surface worker failures in the test
            errors.append(exc)

    workers = [threading.Thread(target=worker) for _ in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join(timeout=60)
    assert not errors, errors
    assert len(results) == threads * per_thread
    return results


def _duplicates(values: list[str]) -> int:
    return len(values) - len(set(values))


def _install_clock(monkeypatch, module, step: timedelta) -> None:
    """Patch the clock both where the generator used to read it (the
    module) and where the shared helper reads it (src.core.timestamps)."""
    clock = _CoarseClock(step)
    monkeypatch.setattr(module, "datetime", clock, raising=False)
    monkeypatch.setattr(timestamps_mod, "datetime", clock)


GENERATORS = [
    pytest.param(write_memory, "_next_timestamp", timedelta(microseconds=1), id="write_memory"),
    pytest.param(session, "_now_id", timedelta(microseconds=1), id="session"),
    pytest.param(project, "_now_id", timedelta(microseconds=1), id="project"),
    pytest.param(task_service, "next_timestamp", timedelta(microseconds=1), id="task_service"),
    pytest.param(timer_service, "_next_timestamp", timedelta(microseconds=1), id="timer_service"),
    pytest.param(state_service, "_next_state_timestamp", timedelta(seconds=1), id="state_service"),
]


@pytest.mark.parametrize("module, func_name, step", GENERATORS)
def test_concurrent_generation_yields_no_duplicates(monkeypatch, module, func_name, step):
    _install_clock(monkeypatch, module, step)
    fn = getattr(module, func_name)

    values = _race(fn)

    assert _duplicates(values) == 0


def test_concurrent_lodestone_writes_get_distinct_ids(monkeypatch, tmp_path):
    """Lodestone has no id generator function; exercise write() directly.
    The directory is redirected to tmp_path so these proposed records do
    not count against other tests' lodestone caps."""
    _install_clock(monkeypatch, lodestone_service, timedelta(microseconds=1))
    monkeypatch.setattr(lodestone_service, "_lodestone_dir", lambda: tmp_path)

    def write_one() -> str:
        record = lodestone_service.write(
            value="synthetic value",
            taxonomy_category="character",
            confirmed=False,
        )
        return record["id"]

    ids = _race(write_one, threads=4, per_thread=5)

    assert _duplicates(ids) == 0
    assert len(list(tmp_path.glob("*.json"))) == len(ids)


def test_generated_values_keep_each_module_format_and_timezone():
    """Each module keeps its own format: microsecond for most, second
    precision for the state layer, and UTC for session/project ids."""
    assert len(write_memory._next_timestamp()) == len("2026-01-01T12-00-00-000000")
    assert len(state_service._next_state_timestamp()) == len("2026-01-01T12-00-00")

    before = datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    session_id = datetime.strptime(session._now_id(), US_FORMAT)
    project_id = datetime.strptime(project._now_id(), US_FORMAT)
    assert abs((session_id - before).total_seconds()) < 60
    assert abs((project_id - before).total_seconds()) < 60


# ---------------------------------------------------------------------------
# Positive control
# ---------------------------------------------------------------------------

def test_control_unlocked_check_then_set_collides():
    """The harness must be able to produce the defect. An unlocked
    check-then-set generator on the same fake clock has to collide."""
    clock = _CoarseClock(timedelta(microseconds=1))
    state = {"last": ""}

    def unlocked() -> str:
        while True:
            candidate = clock.now().strftime(US_FORMAT)
            if candidate != state["last"]:
                state["last"] = candidate
                return candidate

    values = _race(unlocked)

    assert _duplicates(values) > 0
