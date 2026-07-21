"""
tests/eval/retry_util.py

A tiny retry-with-backoff helper for the eval judge calls.

The Sonnet/Haiku judges fire many calls back-to-back per run (register alone is
~18). Some sporadically fail on transient conditions - rate limits (429),
network blips - which previously counted as judge_errors and blocked the
baseline. This retries the call a few times with exponential backoff so a
transient failure self-heals before the fail-closed sentinel is used.

Deliberately dependency-free (no anthropic import) so it stays Tier-1 / CI-safe
and can be reused by both tests/eval/judge.py and tests/eval/quality_judges.py.
`sleep` is injectable so tests run instantly.
"""

from __future__ import annotations

import time
from typing import Callable

# Total attempts (initial + retries) and the base backoff in seconds. Backoff
# grows as base * 2**attempt: with base 2.0 that is 2s then 4s between the three
# attempts - enough headroom for a rate-limit window to clear.
DEFAULT_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 2.0


def retry_call(
    fn: Callable,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    sleep: Callable[[float], None] | None = None,
):
    """Call fn(), retrying on any exception with exponential backoff.

    Returns fn()'s result on the first success. Raises the LAST exception if
    every attempt fails, so the caller's fail-closed fallback (and the
    judge-health guard) still fires on a persistent outage.

    `sleep` defaults to time.sleep, resolved at call time so tests can stub
    time.sleep globally without threading a sleep argument through the judges.
    """
    _sleep = sleep if sleep is not None else time.sleep
    last_exc = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - retry on any transient error
            last_exc = exc
            if attempt < attempts - 1:
                _sleep(base_delay * (2 ** attempt))
    raise last_exc
