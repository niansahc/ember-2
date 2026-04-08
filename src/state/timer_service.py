"""
src/state/timer_service.py

Timer functions built on top of the state layer (BUG-004).

Timers are stored as StateRecord objects with type="timer" in
private_vault/memory/state/. Each timer carries a stable timer_id
(12-char hex) in metadata that links its start record to its eventual
stop record. Both records are append-only — neither is ever modified or
deleted on disk.

Active timer semantics
----------------------
A timer is "active" iff its most recent record (by timestamp) has
metadata.status == "running". Stopped timers leave their original
"running" record on disk forever, but get_active_timers() correctly
filters them out by grouping all records by timer_id and inspecting
only the latest record per group. Literal "filter where status ==
'running'" would surface stopped timers and is wrong.

Detector functions
------------------
detect_start_timer() / detect_stop_timer() / detect_check_timer()
are conservative pattern matchers used by openai_adapter to route
timer-related user messages without invoking the LLM. All three require
the literal word "timer" or an unambiguous timer phrase to fire — false
positives on phrases like "how long is this song?" are intentionally
avoided.

Storage notes
-------------
Timer records use microsecond-precision timestamps generated locally
via _next_timestamp() with the same spin-on-collision guard as
session._now_id() and write_memory._next_timestamp() (BUG-005). This
deviates from StateService.make_record(), which uses second precision
and is itself vulnerable to the same race — a separate follow-up.

The microsecond format `%Y-%m-%dT%H-%M-%S-%f` sorts lexicographically
later than the second-precision format `%Y-%m-%dT%H-%M-%S` for the same
second, so timer records interleave correctly with other state records
in StateResolver's timestamp comparisons.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime
from typing import Optional

from src.state.models import StateRecord
from src.state.state_service import StateService

logger = logging.getLogger("ember.timer_service")


# ---------------------------------------------------------------------------
# Timestamp helper — microsecond precision with spin guard
# ---------------------------------------------------------------------------

# Module-level guard against same-microsecond timestamp collisions when
# multiple timer records are written back-to-back (e.g. in tests, or when
# a user starts and stops a timer in quick succession). Mirrors the
# BUG-005 fix in session._now_id() and task_service.next_timestamp().
_last_timestamp: str = ""


def _next_timestamp() -> str:
    """Return a microsecond-precision timestamp string, guaranteed unique
    per process. Spins on datetime.now() until the result differs from the
    previous return value. Hyphen format matches StateService convention
    for Windows filename safety."""
    global _last_timestamp
    while True:
        candidate = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
        if candidate != _last_timestamp:
            _last_timestamp = candidate
            return candidate


def _new_timer_id() -> str:
    """Return a 12-character hex timer ID."""
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# Public write/read helpers
# ---------------------------------------------------------------------------

def start_timer(
    label: str,
    session_id: str,
    service: StateService | None = None,
) -> StateRecord:
    """Write a new running timer record and return it.

    The returned record carries a fresh timer_id in metadata; callers
    should retain it if they want to stop this specific timer later.
    """
    svc = service or StateService()
    now = _next_timestamp()
    record = StateRecord(
        id=now,
        timestamp=now,
        type="timer",
        text=label,
        source="user_input",
        tags=["timer"],
        metadata={
            "timer_id": _new_timer_id(),
            "started_at": now,
            "session_id": session_id,
            "status": "running",
        },
    )
    svc.write(record)
    logger.info("[TIMER] Started '%s' (id=%s)", label, record.metadata["timer_id"])
    return record


def get_active_timers(
    service: StateService | None = None,
) -> list[StateRecord]:
    """Return all currently-running timers, newest first.

    A timer is "active" iff its latest record (grouped by timer_id) has
    metadata.status == "running". Stopped timers are filtered out.

    Uses read_all() + in-memory filter rather than read_by_category() so
    that any service implementing the minimal read_all() interface
    (including the lightweight fakes in test_state_staleness.py) works.
    """
    svc = service or StateService()
    all_records = [r for r in svc.read_all() if r.type == "timer"]
    if not all_records:
        return []

    # Group by timer_id, take the latest record per group.
    latest_per_id: dict[str, StateRecord] = {}
    for record in all_records:
        timer_id = (record.metadata or {}).get("timer_id")
        if not timer_id:
            continue
        existing = latest_per_id.get(timer_id)
        if existing is None or record.timestamp > existing.timestamp:
            latest_per_id[timer_id] = record

    active = [
        r for r in latest_per_id.values()
        if (r.metadata or {}).get("status") == "running"
    ]
    active.sort(key=lambda r: r.timestamp, reverse=True)
    return active


def stop_timer(
    timer_id: str,
    service: StateService | None = None,
) -> StateRecord | None:
    """Write a stopped record for the given timer_id.

    Returns the new stop record, or None if no running timer with that
    id is found. Idempotent: stopping an already-stopped timer is a
    no-op that returns None.
    """
    svc = service or StateService()
    active = get_active_timers(service=svc)
    target = next(
        (r for r in active if (r.metadata or {}).get("timer_id") == timer_id),
        None,
    )
    if target is None:
        return None

    now = _next_timestamp()
    record = StateRecord(
        id=now,
        timestamp=now,
        type="timer",
        text=target.text,
        source="user_input",
        tags=["timer", "stopped"],
        metadata={
            **target.metadata,
            "stopped_at": now,
            "status": "stopped",
        },
    )
    svc.write(record)
    logger.info("[TIMER] Stopped '%s' (id=%s)", target.text, timer_id)
    return record


# ---------------------------------------------------------------------------
# Elapsed time formatting
# ---------------------------------------------------------------------------

def format_elapsed(started_at: str) -> str:
    """Convert a started_at timestamp string into a human-readable
    elapsed-time phrase. Tolerates both microsecond and second precision
    in the hyphen-format used by the state layer.

    Buckets:
      < 1 minute    → "less than a minute ago"
      < 1 hour      → "{N} minutes ago"
      exact hour    → "{N} hours ago"
      mixed         → "{N} hours {M} minutes ago"
    """
    started_dt = _parse_state_timestamp(started_at)
    if started_dt is None:
        return "just now"

    elapsed_seconds = (datetime.now() - started_dt).total_seconds()
    if elapsed_seconds < 60:
        return "less than a minute ago"

    minutes_total = int(elapsed_seconds // 60)
    if elapsed_seconds < 3600:
        return "1 minute ago" if minutes_total == 1 else f"{minutes_total} minutes ago"

    hours = int(elapsed_seconds // 3600)
    rem_minutes = int((elapsed_seconds % 3600) // 60)

    if rem_minutes == 0:
        return "1 hour ago" if hours == 1 else f"{hours} hours ago"

    hour_word = "1 hour" if hours == 1 else f"{hours} hours"
    minute_word = "1 minute" if rem_minutes == 1 else f"{rem_minutes} minutes"
    return f"{hour_word} {minute_word} ago"


def _parse_state_timestamp(value: str) -> datetime | None:
    """Parse a state-layer timestamp string back into a datetime.

    The state layer stores timestamps in hyphen-only format
    `YYYY-MM-DDTHH-MM-SS[-ffffff]` for Windows filename safety. This
    helper reverses just enough of that to feed datetime.fromisoformat.
    Returns None on any parse failure — callers fall back gracefully.
    """
    if not value or not isinstance(value, str):
        return None
    date_part, sep, time_part = value.partition("T")
    if not sep:
        return None
    components = time_part.split("-")
    if len(components) < 3:
        return None
    hh, mm, ss = components[0], components[1], components[2]
    iso = f"{date_part}T{hh}:{mm}:{ss}"
    if len(components) >= 4 and components[3]:
        iso += f".{components[3]}"
    try:
        return datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Detectors — pattern matchers for openai_adapter routing
# ---------------------------------------------------------------------------

# Conservative — every pattern requires the literal word "timer".
START_TIMER_PATTERNS = (
    re.compile(
        r"(?:can you |please |could you )?(?:start|set|begin) (?:a |the )?timer "
        r"(?:for |to |called |named |on |about )(.+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:can you |please |could you )?(?:start|set|begin) (?:a |the )?(.+?) timer\b",
        re.IGNORECASE,
    ),
)

STOP_TIMER_PATTERNS = (
    re.compile(r"\b(?:stop|end|cancel|kill|finish)\b[^.?!]{0,40}\btimer\b", re.IGNORECASE),
    re.compile(r"\btimer\b[^.?!]{0,40}\b(?:stop|end|done|finished)\b", re.IGNORECASE),
)

CHECK_TIMER_PATTERNS = (
    re.compile(r"\btimer\b[^.?!]{0,40}\b(?:check|status|left|elapsed|going)\b", re.IGNORECASE),
    re.compile(r"\b(?:check|how long|how much).{0,30}\btimer\b", re.IGNORECASE),
)


def detect_start_timer(user_message: str) -> str | None:
    """Return the extracted timer label if the message is a start request,
    else None. Conservative — both patterns require the word 'timer'."""
    if not user_message or len(user_message.strip()) < 8:
        return None
    text = user_message.strip()
    for pattern in START_TIMER_PATTERNS:
        match = pattern.search(text)
        if match:
            label = match.group(1).strip().rstrip(".!?,;").strip()
            # Strip trailing common phrases that shouldn't be in the label.
            label = re.sub(r"\s+(?:please|now|please now)$", "", label, flags=re.IGNORECASE)
            if label:
                return label
    return None


def detect_stop_timer(user_message: str) -> bool:
    """True if the message looks like a stop-the-timer request."""
    if not user_message:
        return False
    return any(p.search(user_message) for p in STOP_TIMER_PATTERNS)


def detect_check_timer(user_message: str) -> bool:
    """True if the message looks like a how-long-has-it-been query."""
    if not user_message:
        return False
    return any(p.search(user_message) for p in CHECK_TIMER_PATTERNS)
