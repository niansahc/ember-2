"""
tests/test_record_versioning.py

src/memory/versioning.py reads the version counter that session, project
and lodestone records carry. A missing counter (a record written before
it existed) reads as 0. An integral value stored as a float or a digit
string reads as its integer; a bool, a fraction or other text reads as 0,
so a hand-edited record cannot outrank real versions by accident.
"""

import logging

import pytest

import src.memory.project as project
import src.memory.session as session
from src.memory.versioning import coerce_version, metadata_rank


@pytest.mark.parametrize("stored, expected", [
    (None, 0),
    (0, 0),
    (3, 3),
    (3.0, 3),
    ("3", 3),
    (" 3 ", 3),
    (True, 0),
    (False, 0),
    (2.5, 0),
    ("three", 0),
    ("-1", 0),
    ([3], 0),
])
def test_coerce_version(stored, expected):
    assert coerce_version(stored) == expected


def test_unreadable_version_is_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="ember.versioning"):
        coerce_version(2.5)
    assert "float" in caplog.text


def test_control_missing_version_is_not_logged(caplog):
    """Positive control for the log above: legacy records have no
    counter, and reading one stays quiet."""
    with caplog.at_level(logging.WARNING, logger="ember.versioning"):
        coerce_version(None)
        coerce_version(4)
    assert caplog.text == ""


def test_metadata_rank_orders_by_version_then_timestamp():
    old = {"timestamp": "2026-01-01T12:00:09", "metadata": {"version": 1}}
    new = {"timestamp": "2026-01-01T12:00:01", "metadata": {"version": 2}}
    tie_later = {"timestamp": "2026-01-01T12:00:05", "metadata": {"version": 2}}
    assert metadata_rank(new) > metadata_rank(old)
    assert metadata_rank(tie_later) > metadata_rank(new)


@pytest.mark.parametrize("resolve, key", [
    (session._resolve_sessions, "session_id"),
    (project._resolve_projects, "project_id"),
])
def test_integral_float_version_outranks_a_lower_int(resolve, key):
    records = [
        {"timestamp": "2026-01-01T12:00:01", "text": "new", "metadata": {key: "id_test_001", "version": 3.0}},
        {"timestamp": "2026-01-01T12:00:02", "text": "old", "metadata": {key: "id_test_001", "version": 1}},
    ]
    assert resolve(records)["id_test_001"]["text"] == "new"


@pytest.mark.parametrize("resolve, key", [
    (session._resolve_sessions, "session_id"),
    (project._resolve_projects, "project_id"),
])
def test_bool_version_does_not_outrank_version_one(resolve, key):
    records = [
        {"timestamp": "2026-01-01T12:00:02", "text": "bool", "metadata": {key: "id_test_001", "version": True}},
        {"timestamp": "2026-01-01T12:00:01", "text": "real", "metadata": {key: "id_test_001", "version": 1}},
    ]
    assert resolve(records)["id_test_001"]["text"] == "real"
