"""
tests/test_index_cache.py

Tests for vector index caching.
"""

import json
import tempfile
from pathlib import Path

import pytest

from src.retrieval.vector_index import VectorIndex, _index_cache, clear_index_cache


@pytest.fixture(autouse=True)
def clean_cache():
    """Clear the index cache before and after each test."""
    clear_index_cache()
    yield
    clear_index_cache()


def make_index_file(tmp_path: Path, name: str = "test_index.json", data: list | None = None):
    """Create a test index file and return its path."""
    content = data or [
        {"text": "hello world", "embedding": [0.1, 0.2, 0.3], "file_path": "/fake/path.json"},
    ]
    path = tmp_path / name
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


class TestCacheHitMiss:
    """Test that loading the same index twice returns cached version."""

    def test_first_load_is_cache_miss(self, tmp_path):
        vi = VectorIndex()
        path = make_index_file(tmp_path)
        result = vi.load_index(path)
        assert len(result) == 1
        assert str(path) in _index_cache

    def test_second_load_is_cache_hit(self, tmp_path):
        vi = VectorIndex()
        path = make_index_file(tmp_path)

        # First load
        result1 = vi.load_index(path)
        # Modify the file on disk — cache should ignore it
        path.write_text(json.dumps([{"text": "modified"}]), encoding="utf-8")
        # Second load — should return cached version, not modified file
        result2 = vi.load_index(path)

        assert result1 is result2  # Same object reference
        assert result2[0]["text"] == "hello world"  # Original, not modified

    def test_different_paths_cached_independently(self, tmp_path):
        vi = VectorIndex()
        path1 = make_index_file(tmp_path, "index_a.json", [{"text": "a", "embedding": [1]}])
        path2 = make_index_file(tmp_path, "index_b.json", [{"text": "b", "embedding": [2]}])

        result1 = vi.load_index(path1)
        result2 = vi.load_index(path2)

        assert result1[0]["text"] == "a"
        assert result2[0]["text"] == "b"
        assert len(_index_cache) == 2


class TestClearCache:
    """Test that clear_index_cache forces a fresh load."""

    def test_clear_all(self, tmp_path):
        vi = VectorIndex()
        path = make_index_file(tmp_path)
        vi.load_index(path)
        assert len(_index_cache) == 1

        clear_index_cache()
        assert len(_index_cache) == 0

    def test_clear_specific_path(self, tmp_path):
        vi = VectorIndex()
        path1 = make_index_file(tmp_path, "a.json")
        path2 = make_index_file(tmp_path, "b.json")
        vi.load_index(path1)
        vi.load_index(path2)
        assert len(_index_cache) == 2

        clear_index_cache(str(path1))
        assert len(_index_cache) == 1
        assert str(path2) in _index_cache

    def test_reload_after_clear_reads_fresh_data(self, tmp_path):
        vi = VectorIndex()
        path = make_index_file(tmp_path, data=[{"text": "original", "embedding": [1]}])

        # First load
        result1 = vi.load_index(path)
        assert result1[0]["text"] == "original"

        # Modify on disk and clear cache
        path.write_text(json.dumps([{"text": "updated", "embedding": [2]}]), encoding="utf-8")
        clear_index_cache()

        # Reload — should get updated data
        result2 = vi.load_index(path)
        assert result2[0]["text"] == "updated"


class TestSaveInvalidatesCache:
    """Test that writing an index clears its cache entry."""

    def test_save_clears_cache(self, tmp_path):
        vi = VectorIndex()
        path = make_index_file(tmp_path)

        # Load to populate cache
        vi.load_index(path)
        assert str(path) in _index_cache

        # Save new data — should invalidate cache
        vi.save_index(path, [{"text": "new data"}])
        assert str(path) not in _index_cache

    def test_load_after_save_reads_new_data(self, tmp_path):
        vi = VectorIndex()
        path = make_index_file(tmp_path, data=[{"text": "old", "embedding": [1]}])

        vi.load_index(path)
        vi.save_index(path, [{"text": "new", "embedding": [2]}])
        result = vi.load_index(path)

        assert result[0]["text"] == "new"
