"""
tests/test_timer_service.py

Tests for the timer feature built on top of the state layer (BUG-004).

All tests use a temporary vault via tmp_path so they never touch the
real private_vault. Generic identifiers only — no vault contents.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.state.state_service import StateService
from src.state.timer_service import (
    detect_check_timer,
    detect_start_timer,
    detect_stop_timer,
    format_elapsed,
    get_active_timers,
    start_timer,
    stop_timer,
)


@pytest.fixture
def service(tmp_path):
    """A StateService bound to a fresh temporary vault."""
    return StateService(vault_path=tmp_path)


# ---------------------------------------------------------------------------
# start_timer
# ---------------------------------------------------------------------------

class TestStartTimer:
    def test_writes_running_state_record(self, service):
        record = start_timer(label="focus block", session_id="sess_test_001", service=service)
        assert record.type == "timer"
        assert record.text == "focus block"
        assert record.source == "user_input"
        assert "timer" in record.tags
        assert record.metadata["status"] == "running"
        assert record.metadata["session_id"] == "sess_test_001"
        assert "timer_id" in record.metadata
        assert "started_at" in record.metadata

    def test_record_is_persisted_to_disk(self, service):
        start_timer(label="brew tea", session_id="sess_test_002", service=service)
        # Re-read from the same vault via the service.
        all_records = service.read_by_category("timer")
        assert len(all_records) == 1
        assert all_records[0].text == "brew tea"


# ---------------------------------------------------------------------------
# get_active_timers
# ---------------------------------------------------------------------------

class TestGetActiveTimers:
    def test_returns_only_running_timers(self, service):
        a = start_timer(label="alpha", session_id="sess_test_001", service=service)
        b = start_timer(label="bravo", session_id="sess_test_001", service=service)
        # Stop one of them
        stop_timer(timer_id=a.metadata["timer_id"], service=service)

        active = get_active_timers(service=service)
        assert len(active) == 1
        assert active[0].text == "bravo"
        assert active[0].metadata["timer_id"] == b.metadata["timer_id"]

    def test_returns_empty_when_no_timers(self, service):
        assert get_active_timers(service=service) == []

    def test_returns_empty_when_all_stopped(self, service):
        a = start_timer(label="solo", session_id="sess_test_003", service=service)
        stop_timer(timer_id=a.metadata["timer_id"], service=service)
        assert get_active_timers(service=service) == []


# ---------------------------------------------------------------------------
# stop_timer
# ---------------------------------------------------------------------------

class TestStopTimer:
    def test_stop_writes_stopped_record_and_removes_from_active(self, service):
        a = start_timer(label="pomodoro", session_id="sess_test_004", service=service)
        timer_id = a.metadata["timer_id"]

        # Sanity: active list contains it
        assert any(r.metadata.get("timer_id") == timer_id for r in get_active_timers(service=service))

        stop_record = stop_timer(timer_id=timer_id, service=service)
        assert stop_record is not None
        assert stop_record.type == "timer"
        assert stop_record.metadata["status"] == "stopped"
        assert stop_record.metadata["timer_id"] == timer_id
        assert "stopped_at" in stop_record.metadata

        # And the timer is no longer active
        active_ids = {r.metadata.get("timer_id") for r in get_active_timers(service=service)}
        assert timer_id not in active_ids

    def test_stop_unknown_timer_returns_none(self, service):
        result = stop_timer(timer_id="does_not_exist", service=service)
        assert result is None

    def test_double_stop_is_idempotent(self, service):
        a = start_timer(label="meeting", session_id="sess_test_005", service=service)
        timer_id = a.metadata["timer_id"]
        first = stop_timer(timer_id=timer_id, service=service)
        second = stop_timer(timer_id=timer_id, service=service)
        assert first is not None
        assert second is None  # Already stopped, no longer active


# ---------------------------------------------------------------------------
# format_elapsed
# ---------------------------------------------------------------------------

def _ago(delta: timedelta) -> str:
    """Build a hyphen-format state timestamp string for `delta` ago."""
    return (datetime.now() - delta).strftime("%Y-%m-%dT%H-%M-%S-%f")


class TestFormatElapsed:
    def test_under_one_minute(self):
        assert format_elapsed(_ago(timedelta(seconds=20))) == "less than a minute ago"

    def test_minutes_only(self):
        assert format_elapsed(_ago(timedelta(minutes=5))) == "5 minutes ago"

    def test_singular_minute(self):
        # Use 75 seconds to land cleanly inside the "1 minute" bucket.
        assert format_elapsed(_ago(timedelta(seconds=75))) == "1 minute ago"

    def test_exact_hour(self):
        # 3600 seconds — but use a few extra to avoid the 59:59 boundary.
        assert format_elapsed(_ago(timedelta(hours=2, seconds=5))) == "2 hours ago"

    def test_singular_hour(self):
        assert format_elapsed(_ago(timedelta(hours=1, seconds=5))) == "1 hour ago"

    def test_hours_and_minutes(self):
        result = format_elapsed(_ago(timedelta(hours=1, minutes=15)))
        assert result == "1 hour 15 minutes ago"

    def test_multiple_hours_and_minutes(self):
        result = format_elapsed(_ago(timedelta(hours=3, minutes=42)))
        assert result == "3 hours 42 minutes ago"

    def test_invalid_input_returns_safe_fallback(self):
        assert format_elapsed("not a timestamp") == "just now"
        assert format_elapsed("") == "just now"


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

class TestDetectors:
    def test_detect_start_basic(self):
        assert detect_start_timer("start a timer for laundry") == "laundry"

    def test_detect_start_polite(self):
        assert detect_start_timer("can you start a timer for the bread proof") == "the bread proof"

    def test_detect_start_alternate_phrasing(self):
        assert detect_start_timer("set a timer called focus block") == "focus block"

    def test_detect_start_returns_none_on_unrelated(self):
        assert detect_start_timer("how long is this song?") is None
        assert detect_start_timer("tell me a joke") is None

    def test_detect_stop_true(self):
        assert detect_stop_timer("stop the timer please") is True
        assert detect_stop_timer("can you cancel the timer") is True

    def test_detect_stop_false(self):
        assert detect_stop_timer("how are you") is False
        # No timer word — must not match.
        assert detect_stop_timer("stop talking") is False

    def test_detect_check_true(self):
        assert detect_check_timer("how long has the timer been going") is True
        assert detect_check_timer("timer check") is True

    def test_detect_check_false(self):
        assert detect_check_timer("hello") is False
