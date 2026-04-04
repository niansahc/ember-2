"""
Tests for index.html cache invalidation on mtime change.
"""

import time
from pathlib import Path
from unittest.mock import patch


def test_cache_invalidates_on_mtime_change(tmp_path: Path):
    """Verify that _get_index_html() serves fresh content after UI rebuild."""
    # Create a fake ui/ directory with index.html
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir()
    index_file = ui_dir / "index.html"
    index_file.write_text(
        '<html><head><script src="old.js"></script></head><body></body></html>',
        encoding="utf-8",
    )

    # Patch _UI_DIR and clear the cache
    import src.api.main as main_module
    original_ui_dir = main_module._UI_DIR
    original_cached_html = main_module._cached_index_html
    original_cached_mtime = main_module._cached_index_mtime

    try:
        main_module._UI_DIR = ui_dir
        main_module._cached_index_html = None
        main_module._cached_index_mtime = 0.0

        # First call — caches the content
        html1 = main_module._get_index_html()
        assert "old.js" in html1

        # Same call — should return cached (same mtime)
        html2 = main_module._get_index_html()
        assert html2 is html1  # same object = cache hit

        # Simulate UI rebuild — write new content with different mtime
        time.sleep(0.1)  # ensure mtime differs
        index_file.write_text(
            '<html><head><script src="new.js"></script></head><body></body></html>',
            encoding="utf-8",
        )

        # Next call — should detect mtime change and return fresh content
        html3 = main_module._get_index_html()
        assert "new.js" in html3
        assert "old.js" not in html3

    finally:
        # Restore original state
        main_module._UI_DIR = original_ui_dir
        main_module._cached_index_html = original_cached_html
        main_module._cached_index_mtime = original_cached_mtime
