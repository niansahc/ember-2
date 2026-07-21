"""
tests/eval/test_retry_util.py

Unit tests for the judge retry helper. Pure - runs in the default Tier-1 suite.
`sleep` is stubbed so the tests never actually wait.
"""

import pytest

from tests.eval.retry_util import retry_call


def test_returns_first_success_without_retrying():
    calls = {"n": 0}

    def once():
        calls["n"] += 1
        return "ok"

    assert retry_call(once, sleep=lambda s: None) == "ok"
    assert calls["n"] == 1


def test_recovers_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("429 rate limited")
        return "recovered"

    result = retry_call(flaky, attempts=3, sleep=lambda s: None)
    assert result == "recovered"
    assert calls["n"] == 3


def test_raises_last_exception_after_exhausting_attempts():
    def always_fail():
        raise RuntimeError("persistent outage")

    with pytest.raises(RuntimeError, match="persistent outage"):
        retry_call(always_fail, attempts=3, sleep=lambda s: None)


def test_backoff_grows_exponentially():
    delays = []

    def always_fail():
        raise RuntimeError("down")

    with pytest.raises(RuntimeError):
        retry_call(always_fail, attempts=3, base_delay=2.0, sleep=delays.append)
    # Two waits between three attempts: 2s then 4s.
    assert delays == [2.0, 4.0]
