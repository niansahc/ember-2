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
  - src.core.timestamps' `datetime` is replaced by a fake clock that advances one
    step every READS_PER_TICK reads, so concurrent readers see the same
    value, and
  - the value's <= comparison, and the __ne__ of the str its strftime()
    returns, sleep before comparing, which releases the GIL between the
    check and the set. The right-hand operand is bound before the sleep, so
    a second thread that compares during the sleep still sees the stale
    `_last`.

Without a lock that held across read-compare-set, collisions are certain.
With one, the other threads block on the lock and every value is unique.

The positive control runs the same harness against an unlocked
check-then-set generator defined here and asserts it does collide, so the
zero-duplicate assertions cannot pass vacuously.
"""

from __future__ import annotations

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
from tests.conftest import TickClock, install_tick_clock, run_in_threads

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
    """A datetime that yields the GIL inside its own <= comparison and
    whose strftime result yields inside comparisons, so both the shipped
    compare-then-set (on datetimes) and the control's (on strings) have a
    yield point between the check and the set."""

    def __le__(self, other):
        time.sleep(0.001)
        return super().__le__(other)

    def strftime(self, fmt):
        return _YieldingStr(super().strftime(fmt))


def _race(fn, threads: int = THREADS, per_thread: int = PER_THREAD) -> list[str]:
    """Call fn per_thread times on each of `threads` threads, started together."""
    batches, errors = run_in_threads(lambda _i: [fn() for _ in range(per_thread)], threads)
    assert not errors, errors
    results = [value for batch in batches for value in batch]
    assert len(results) == threads * per_thread
    return results


def _duplicates(values: list[str]) -> int:
    return len(values) - len(set(values))


def _install_clock(monkeypatch, step: timedelta) -> None:
    install_tick_clock(monkeypatch, step, READS_PER_TICK, _FakeDateTime)


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
    _install_clock(monkeypatch, step)
    fn = getattr(module, func_name)

    values = _race(fn)

    assert _duplicates(values) == 0


def test_concurrent_lodestone_writes_get_distinct_ids(monkeypatch, tmp_path):
    """Lodestone has no id generator function; exercise write() directly.
    The directory is redirected to tmp_path so these proposed records do
    not count against other tests' lodestone caps."""
    _install_clock(monkeypatch, timedelta(microseconds=1))
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
    clock = TickClock(timedelta(microseconds=1), READS_PER_TICK, _FakeDateTime)
    state = {"last": ""}

    def unlocked() -> str:
        while True:
            candidate = clock.now().strftime(US_FORMAT)
            if candidate != state["last"]:
                state["last"] = candidate
                return candidate

    values = _race(unlocked)

    assert _duplicates(values) > 0


def test_control_unlocked_logical_advance_collides():
    """The same harness against the shipped algorithm (max(now, last + unit))
    with the lock removed: the <= yield point lets two threads read the same
    tick and both return it, so the race tests above exercise the lock."""
    clock = TickClock(timedelta(microseconds=1), READS_PER_TICK, _FakeDateTime)
    unit = timedelta(microseconds=1)
    state = {"last": None}

    def unlocked() -> str:
        now = clock.now()
        if state["last"] is not None and now <= state["last"]:
            now = state["last"] + unit
        state["last"] = now
        return now.strftime(US_FORMAT)

    values = _race(unlocked)

    assert _duplicates(values) > 0


# ---------------------------------------------------------------------------
# Logical advance: no call waits on the clock
# ---------------------------------------------------------------------------

class _FrozenClock:
    """Stands in for `datetime` in src.core.timestamps. now() returns
    `value` until a test moves it, and counts reads. Past `budget` reads it
    raises, so a generator that waits for the clock to move fails instead
    of hanging."""

    def __init__(self, budget: int) -> None:
        self.value = datetime(2026, 1, 1, 12, 0, 0, 500000)
        self.reads = 0
        self._budget = budget
        self._lock = threading.Lock()

    def now(self, tz=None):
        with self._lock:
            self.reads += 1
            if self.reads > self._budget:
                raise AssertionError("clock read budget exhausted: caller is waiting on the clock")
        return self.value.replace(tzinfo=tz)

    def __getattr__(self, name):
        return getattr(datetime, name)


K = 50

LOGICAL_FORMATS = [
    pytest.param(US_FORMAT, timedelta(microseconds=1), id="microsecond"),
    pytest.param(SECOND_FORMAT, timedelta(seconds=1), id="second"),
]


def _expected(clock: _FrozenClock, fmt: str, unit: timedelta, count: int) -> list[str]:
    start = clock.value if unit < timedelta(seconds=1) else clock.value.replace(microsecond=0)
    return [(start + unit * i).strftime(fmt) for i in range(count)]


@pytest.mark.parametrize("fmt, unit", LOGICAL_FORMATS)
def test_frozen_clock_sequential_calls_advance_one_unit_each(monkeypatch, fmt, unit):
    clock = _FrozenClock(budget=K)
    monkeypatch.setattr(timestamps_mod, "datetime", clock)
    source = timestamps_mod.UniqueTimestampSource(fmt)

    values = [source.next() for _ in range(K)]

    assert values == _expected(clock, fmt, unit, K)
    assert clock.reads == K


@pytest.mark.parametrize("fmt, unit", LOGICAL_FORMATS)
def test_frozen_clock_concurrent_calls_are_distinct_and_consecutive(monkeypatch, fmt, unit):
    clock = _FrozenClock(budget=K)
    monkeypatch.setattr(timestamps_mod, "datetime", clock)
    source = timestamps_mod.UniqueTimestampSource(fmt)

    values, errors = run_in_threads(lambda _i: source.next(), K)

    assert not errors, errors
    assert sorted(values) == _expected(clock, fmt, unit, K)
    assert clock.reads == K


def test_values_follow_the_clock_once_it_passes_the_last_value(monkeypatch, caplog):
    """A burst runs ahead of a frozen clock; once the clock is later than
    the last value, values are the clock again rather than last + 1. Each
    logical advance logs once at DEBUG."""
    caplog.set_level("DEBUG", logger="ember.timestamps")
    clock = _FrozenClock(budget=10)
    monkeypatch.setattr(timestamps_mod, "datetime", clock)
    source = timestamps_mod.UniqueTimestampSource(SECOND_FORMAT)

    burst = [source.next() for _ in range(3)]
    clock.value += timedelta(seconds=10)
    after = source.next()

    assert burst == ["2026-01-01T12-00-00", "2026-01-01T12-00-01", "2026-01-01T12-00-02"]
    assert after == "2026-01-01T12-00-10"
    advances = [r for r in caplog.records if "Advanced logically" in r.getMessage()]
    assert len(advances) == 2


def test_clock_stepping_backward_still_yields_increasing_values(monkeypatch):
    clock = _FrozenClock(budget=10)
    monkeypatch.setattr(timestamps_mod, "datetime", clock)
    source = timestamps_mod.UniqueTimestampSource(SECOND_FORMAT)

    first = source.next()
    clock.value -= timedelta(hours=1)
    second = source.next()

    assert second > first


def test_control_spinning_generator_exhausts_the_read_budget(monkeypatch):
    """Positive control for the read-count assertions: the previous
    spin-until-different algorithm on the same frozen clock keeps reading
    and trips the budget, so `reads == K` can fail."""
    clock = _FrozenClock(budget=K)
    last = {"value": ""}

    def spinning() -> str:
        while True:
            candidate = clock.now().strftime(SECOND_FORMAT)
            if candidate != last["value"]:
                last["value"] = candidate
                return candidate

    spinning()
    with pytest.raises(AssertionError, match="budget exhausted"):
        spinning()


def test_real_clock_state_format_burst_does_not_wait():
    """K concurrent second-precision calls on the real clock: K distinct
    values, returned in far less than the K seconds a waiting generator
    would need."""
    source = timestamps_mod.UniqueTimestampSource(SECOND_FORMAT)
    burst = 10

    started = time.perf_counter()
    values, errors = run_in_threads(lambda _i: source.next(), burst)
    sequential = [source.next() for _ in range(burst)]
    elapsed = time.perf_counter() - started

    assert not errors, errors
    assert _duplicates(values + sequential) == 0
    assert sequential == sorted(sequential)
    assert min(sequential) > max(values)
    assert elapsed < 1.0, f"{2 * burst} calls took {elapsed:.2f}s"


def test_format_without_seconds_is_rejected():
    with pytest.raises(ValueError):
        timestamps_mod.UniqueTimestampSource("%Y-%m-%d")
