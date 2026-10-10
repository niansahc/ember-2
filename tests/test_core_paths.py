"""tests/test_core_paths.py -- resolve_inside, the single containment check
behind UI file serving, upload saving and import path validation."""
from pathlib import Path

from src.core.paths import resolve_inside


def test_relative_child_resolves(tmp_path: Path):
    (tmp_path / "a").mkdir()
    assert resolve_inside(tmp_path, "a") == (tmp_path / "a").resolve()
    assert resolve_inside(tmp_path, "a/b.txt") == (tmp_path / "a" / "b.txt").resolve()


def test_parent_segment_and_absolute_are_rejected(tmp_path: Path):
    outside = tmp_path.parent / "elsewhere.txt"
    assert resolve_inside(tmp_path, "../elsewhere.txt") is None
    assert resolve_inside(tmp_path, str(outside)) is None
    assert resolve_inside(tmp_path, outside.as_posix()) is None


def test_direct_child_rejects_nesting(tmp_path: Path):
    assert resolve_inside(tmp_path, "file.txt", direct_child=True) == (tmp_path / "file.txt").resolve()
    assert resolve_inside(tmp_path, "sub/file.txt", direct_child=True) is None
    assert resolve_inside(tmp_path, "", direct_child=True) is None


def test_unresolvable_input_returns_none(tmp_path: Path):
    assert resolve_inside(tmp_path, "file" + chr(0) + ".txt") is None
