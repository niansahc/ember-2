"""
src/core/timestamps.py

Shared vault-record timestamp parsing and generation.

Parsing. Three formats appear across the corpus and are handled
independently in three call sites today (ranker._parse_age_days,
prompt_builder._parse_timestamp, and -- until this fix --
tiering_service._recency_score, which only handled one of the three). This
module is the parser for a fourth call site (_recency_score) rather than a
consolidation of the other two: those are working and tested, and folding
them in here is out of scope for the change that introduced this file. See
ADR-015 amendment, implementation step 4.

Generation. UniqueTimestampSource is the one implementation of the
"never return the same timestamp twice" guard that record writers use to
name files `{timestamp}.json` (BUG-005). Each writer module used to carry
its own unlocked check-then-set copy, which two threads could pass at once
and both return the same value (ultrareview #280). It advances logically
rather than waiting on the clock, so no caller blocks for a clock tick.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone, tzinfo

logger = logging.getLogger("ember.timestamps")


def parse_vault_timestamp(timestamp: str | None) -> datetime | None:
    """Parse a vault-record timestamp into a timezone-aware datetime (UTC).

    Accepts three formats, in order:
    1. Unix epoch as a numeric string (e.g. "1715284775.822009").
    2. The vault canonical hyphenated form ``YYYY-MM-DDTHH-MM-SS``.
    3. Standard ISO 8601 (``YYYY-MM-DDTHH:MM:SS`` with optional Z / offset).

    Returns None when nothing parses.
    """
    if not timestamp:
        return None

    stripped = timestamp.strip()
    if not stripped:
        return None

    # Format 1: numeric-only string -> Unix epoch. Checked first so a
    # decimal epoch string isn't mis-truncated by the hyphenated branch.
    if stripped.replace(".", "", 1).isdigit():
        try:
            epoch = float(stripped)
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (ValueError, OSError, OverflowError):
            return None

    # Format 2: vault canonical hyphenated "YYYY-MM-DDTHH-MM-SS[-ffffff]".
    try:
        if "T" in stripped:
            date_part, time_part = stripped.split("T", 1)
            segments = time_part.split("-")
            if len(segments) >= 3:
                colon_time = f"{segments[0]}:{segments[1]}:{segments[2]}"
                if len(segments) == 4:
                    colon_time += f".{segments[3]}"
                iso_str = f"{date_part}T{colon_time}"
                dt = datetime.fromisoformat(iso_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
    except (ValueError, IndexError):
        pass

    # Format 3: standard ISO 8601, optional trailing Z / offset.
    try:
        normalized = stripped.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    return None


class UniqueTimestampSource:
    """Formatted wall-clock timestamps, strictly increasing per instance.

    next() returns max(now, last + one unit), where the unit is the format's
    resolution: one microsecond for a format with %f, one second otherwise.
    `now` is truncated to that unit first, so two reads inside one unit
    compare equal. The lock is held across the read, the comparison and the
    assignment, so concurrent callers each get their own value.

    No call waits on the clock. When calls arrive faster than the unit, or
    the wall clock steps backward (DST fall-back, an NTP correction), values
    run ahead of the wall clock by one unit per call until it catches up. For
    the second-precision state format, a burst of K records is stamped K
    consecutive seconds starting now.

    Use one instance per set of records that share a directory and format.
    Values are unique only within one instance and one process.
    """

    def __init__(self, fmt: str, tz: tzinfo | None = None) -> None:
        if "%f" in fmt:
            self._unit = timedelta(microseconds=1)
        elif "%S" in fmt:
            self._unit = timedelta(seconds=1)
        else:
            raise ValueError(f"timestamp format needs %S or %f: {fmt!r}")
        self._fmt = fmt
        self._tz = tz
        self._lock = threading.Lock()
        self._last: datetime | None = None

    def next(self) -> str:
        """Return a timestamp string later than every previous return value."""
        with self._lock:
            now = datetime.now(self._tz)
            if self._unit >= timedelta(seconds=1):
                now = now.replace(microsecond=0)
            if self._last is not None and now <= self._last:
                now = self._last + self._unit
                logger.debug("[TIMESTAMP] Advanced logically past the clock (%s)", self._fmt)
            self._last = now
            return now.strftime(self._fmt)
