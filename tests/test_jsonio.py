"""
tests/test_jsonio.py

Unit tests for the safe JSON I/O helpers (A3, ADR-039):
  - safe_read_json: explicit corruption handling, raise-or-default policy
  - safe_write_json: atomic write (temp + os.replace), raise on OSError

All tests use tmp_path; no real vault is touched.
"""

import json

import pytest


def test_round_trip(tmp_path):
    from src.core.jsonio import safe_read_json, safe_write_json

    p = tmp_path / "rec.json"
    safe_write_json(p, {"a": 1, "b": ["x", "y"]})
    assert safe_read_json(p) == {"a": 1, "b": ["x", "y"]}


def test_corrupt_read_raises_without_default(tmp_path):
    from src.core.jsonio import safe_read_json, JsonIoError

    p = tmp_path / "broken.json"
    p.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(JsonIoError):
        safe_read_json(p)


def test_corrupt_read_returns_default(tmp_path):
    from src.core.jsonio import safe_read_json

    p = tmp_path / "broken.json"
    p.write_text("{not valid json", encoding="utf-8")
    assert safe_read_json(p, default={}) == {}
    # None is a legitimate default (collection readers skip None) and must not
    # be confused with the raise sentinel.
    assert safe_read_json(p, default=None) is None


def test_missing_file_default_vs_raise(tmp_path):
    from src.core.jsonio import safe_read_json, JsonIoError

    p = tmp_path / "nope.json"
    assert safe_read_json(p, default={}) == {}
    with pytest.raises(JsonIoError):
        safe_read_json(p)


def test_atomic_write_leaves_no_temp_files(tmp_path):
    from src.core.jsonio import safe_write_json

    p = tmp_path / "rec.json"
    safe_write_json(p, {"ok": True})
    # Only the target file remains; no .tmp residue in the directory.
    names = [f.name for f in tmp_path.iterdir()]
    assert names == ["rec.json"]


def test_write_failure_raises(tmp_path):
    from src.core.jsonio import safe_write_json, JsonIoError

    # Make the would-be parent directory a regular file so mkdir fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    target = blocker / "rec.json"
    with pytest.raises(JsonIoError):
        safe_write_json(target, {"a": 1})


def test_existing_file_overwritten_atomically(tmp_path):
    from src.core.jsonio import safe_read_json, safe_write_json

    p = tmp_path / "rec.json"
    safe_write_json(p, {"v": 1})
    safe_write_json(p, {"v": 2})
    assert safe_read_json(p) == {"v": 2}
