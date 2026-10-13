"""
tests/test_log_filename_collisions.py

Per-event log files named by the clock must not overwrite each other.

grounding_check, self_narrative_check, coaching_filter and
SafetyReviewLogger each write one JSON file per event, named by the
second. Two events in the same second got the same name and the second
write_text() replaced the first entry. grounding_check and
self_narrative_check also formatted local time with a "Z" (UTC) suffix.
Each writer now names files from a UTC UniqueTimestampSource, which
advances one second past the clock instead of repeating a value.

The clock is frozen, so every call in a test lands in the same second.
The filenames stay in the second-precision `...Z` format that
tools/eval/coaching_filter_audit.py and tools/eval/policy_trigger_audit.py
parse.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

import src.core.timestamps as timestamps_mod
import src.llm.coaching_filter as coaching_filter
import src.safety.grounding_check as grounding_check
import src.safety.self_narrative_check as self_narrative_check
from src.context.models import ContextPacket
from src.core.timestamps import LOG_FILENAME_FORMAT, utc_log_timestamp_source
from src.safety.models import SafetyReviewResult, SafetyTriggerResult
from src.safety.review_logger import SafetyReviewLogger
from tools.eval.coaching_filter_audit import (
    _parse_filename_timestamp as coaching_audit_parse,
)
from tools.eval.policy_trigger_audit import (
    _parse_filename_timestamp as policy_audit_parse,
)

CALLS = 5

# 12:00:00 UTC. On a machine whose local zone is not UTC, a writer that
# formats local time produces a different hour and fails the name checks.
FROZEN_UTC = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenClock:
    """Stands in for `datetime`. now() always returns FROZEN_UTC, in the
    requested zone (local time when no zone is given). Every other
    attribute delegates to the real datetime class."""

    def now(self, tz=None):
        if tz is None:
            return FROZEN_UTC.astimezone().replace(tzinfo=None)
        return FROZEN_UTC.astimezone(tz)

    def __getattr__(self, name):
        return getattr(datetime, name)


@pytest.fixture
def frozen_clock(monkeypatch):
    clock = _FrozenClock()
    monkeypatch.setattr(timestamps_mod, "datetime", clock)
    # Writers that still read the clock directly (payload fields) see the
    # same frozen instant.
    for module in (grounding_check, self_narrative_check):
        monkeypatch.setattr(module, "datetime", clock)
    return clock


# ---------------------------------------------------------------------------
# Writers: each entry redirects its log directory to tmp_path, gives the
# module a fresh timestamp source (module sources keep state across tests),
# and returns a callable that writes one entry.
# ---------------------------------------------------------------------------

def _fresh_source(monkeypatch, module) -> None:
    monkeypatch.setattr(module, "_log_timestamps", utc_log_timestamp_source())


def _grounding(monkeypatch, tmp_path):
    monkeypatch.setattr(grounding_check, "_LOG_DIR", tmp_path)
    _fresh_source(monkeypatch, grounding_check)
    return lambda: grounding_check.log_grounding_outcome(
        intent_class="factual_recall",
        triggered=True,
        grounded=True,
        revision_triggered=False,
    )


def _self_narrative(monkeypatch, tmp_path):
    monkeypatch.setattr(self_narrative_check, "_LOG_DIR", tmp_path)
    _fresh_source(monkeypatch, self_narrative_check)
    return lambda: self_narrative_check.log_self_narrative_outcome(
        flag_count=1,
        sentence_indices=[0],
        intent_class="default",
    )


def _coaching(monkeypatch, tmp_path):
    monkeypatch.setattr(coaching_filter, "_LOG_DIR", tmp_path)
    _fresh_source(monkeypatch, coaching_filter)
    return lambda: coaching_filter._log_intervention(
        intent_class="default",
        matches=[{"pattern": "synthetic_pattern"}],
        original="Synthetic original segment.",
        result="Synthetic rewritten segment.",
        stage=1,
    )


def _review(monkeypatch, tmp_path):
    import src.safety.review_logger as review_logger

    _fresh_source(monkeypatch, review_logger)
    logger = SafetyReviewLogger(log_dir=tmp_path)
    packet = ContextPacket(user_message="Synthetic user message.")
    trigger = SafetyTriggerResult(triggered=True, triggered_by=["synthetic"])
    review = SafetyReviewResult(triggered=True, outcome="allow", rules=["synthetic"])
    return lambda: logger.log(packet, "Synthetic draft.", trigger, review)


WRITERS = [
    pytest.param(_grounding, "-grounding", id="grounding_check"),
    pytest.param(_self_narrative, "-self_narrative", id="self_narrative_check"),
    pytest.param(_coaching, "", id="coaching_filter"),
    pytest.param(_review, "", id="review_logger"),
]


def _expected_names(suffix: str) -> list[str]:
    return [f"2026-01-01T12-00-0{i}Z{suffix}.json" for i in range(CALLS)]


@pytest.mark.parametrize("make_writer, suffix", WRITERS)
def test_same_second_writes_keep_every_entry(frozen_clock, monkeypatch, tmp_path, make_writer, suffix):
    write = make_writer(monkeypatch, tmp_path)

    for _ in range(CALLS):
        write()

    names = sorted(p.name for p in tmp_path.glob("*.json"))
    assert names == _expected_names(suffix)


def test_filenames_still_parse_in_the_audit_tools():
    """The names asserted above (no-suffix form, as coaching_filter and
    review_logger write them) parse with both audit tools."""
    names = _expected_names("")
    for parse in (coaching_audit_parse, policy_audit_parse):
        parsed = {parse(name) for name in names}
        assert None not in parsed
        assert len(parsed) == CALLS


# ---------------------------------------------------------------------------
# Positive control
# ---------------------------------------------------------------------------

def test_control_raw_clock_naming_overwrites(frozen_clock, tmp_path):
    """The frozen-clock harness must be able to produce the defect: the old
    naming (the clock formatted to the second) gives every call the same
    name, so the "every entry kept" assertion above can fail."""
    for i in range(CALLS):
        name = timestamps_mod.datetime.now(timezone.utc).strftime(LOG_FILENAME_FORMAT)
        (tmp_path / f"{name}.json").write_text(f'{{"entry": {i}}}', encoding="utf-8")

    assert len(list(tmp_path.glob("*.json"))) < CALLS
