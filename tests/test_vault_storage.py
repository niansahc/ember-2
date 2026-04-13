"""
tests/test_vault_storage.py

Tests for vault storage analysis (format_bytes, analyze_vault).
Uses synthetic vault directories via tmp_path — no real vault data.
"""

import json
from pathlib import Path

import pytest

from src.memory.vault_storage import format_bytes, analyze_vault


class TestFormatBytes:
    """Test human-readable byte formatting."""

    def test_bytes(self):
        assert format_bytes(0) == "0 B"
        assert format_bytes(512) == "512 B"
        assert format_bytes(1023) == "1023 B"

    def test_kilobytes(self):
        assert format_bytes(1024) == "1.0 KB"
        assert format_bytes(1536) == "1.5 KB"
        assert format_bytes(10240) == "10.0 KB"

    def test_megabytes(self):
        assert format_bytes(1048576) == "1.0 MB"
        assert format_bytes(5242880) == "5.0 MB"

    def test_gigabytes(self):
        assert format_bytes(1073741824) == "1.0 GB"
        assert format_bytes(2147483648) == "2.0 GB"


class TestAnalyzeVault:
    """Test vault analysis with synthetic directories."""

    def test_empty_vault(self, tmp_path):
        """Empty vault returns zeros gracefully."""
        result = analyze_vault(tmp_path)
        assert result["current_bytes"] == 0
        assert result["current_human"] == "0 B"
        assert result["by_type"] == {}
        assert result["growth_rate_bytes_per_day"] == 0
        assert result["projected_30d_bytes"] == 0
        assert result["sampled_days"] == 0

    def test_nonexistent_path(self, tmp_path):
        """Non-existent vault path returns zeros."""
        result = analyze_vault(tmp_path / "does_not_exist")
        assert result["current_bytes"] == 0

    def test_by_type_breakdown(self, tmp_path):
        """Files in different memory type subdirectories are counted separately."""
        conv_dir = tmp_path / "memory" / "conversation"
        conv_dir.mkdir(parents=True)
        journal_dir = tmp_path / "memory" / "journal"
        journal_dir.mkdir(parents=True)
        state_dir = tmp_path / "memory" / "state"
        state_dir.mkdir(parents=True)

        # Write synthetic files
        (conv_dir / "turn1.json").write_text('{"text": "hello"}')
        (conv_dir / "turn2.json").write_text('{"text": "world, this is longer"}')
        (journal_dir / "entry1.json").write_text('{"text": "journal entry content here"}')
        (state_dir / "priority.json").write_text('{"type": "priority"}')

        result = analyze_vault(tmp_path)

        assert result["current_bytes"] > 0
        assert "conversation" in result["by_type"]
        assert "journal" in result["by_type"]
        assert "state" in result["by_type"]
        assert result["by_type"]["conversation"]["bytes"] > 0
        assert result["by_type"]["journal"]["bytes"] > 0
        assert result["by_type"]["state"]["bytes"] > 0

    def test_total_is_sum_of_types(self, tmp_path):
        """Total bytes equals sum of all type bytes."""
        conv_dir = tmp_path / "memory" / "conversation"
        conv_dir.mkdir(parents=True)
        ref_dir = tmp_path / "memory" / "reflection"
        ref_dir.mkdir(parents=True)

        (conv_dir / "a.json").write_text('{"text": "conversation data"}')
        (ref_dir / "b.json").write_text('{"text": "reflection data here"}')

        result = analyze_vault(tmp_path)

        type_sum = sum(t["bytes"] for t in result["by_type"].values())
        assert result["current_bytes"] == type_sum

    def test_embeddings_counted(self, tmp_path):
        """Embeddings directory is included in total and by_type."""
        emb_dir = tmp_path / "embeddings"
        emb_dir.mkdir(parents=True)
        (emb_dir / "index.db").write_bytes(b"\x00" * 4096)

        result = analyze_vault(tmp_path)

        assert "embeddings" in result["by_type"]
        assert result["by_type"]["embeddings"]["bytes"] == 4096
        assert result["current_bytes"] == 4096

    def test_growth_rate_calculation(self, tmp_path):
        """Growth rate is computed from file timestamps."""
        conv_dir = tmp_path / "memory" / "conversation"
        conv_dir.mkdir(parents=True)

        import os
        import time

        # Write files with different mtimes
        f1 = conv_dir / "old.json"
        f1.write_text('{"text": "old"}')
        # Set mtime to 10 days ago
        old_time = time.time() - (10 * 86400)
        os.utime(f1, (old_time, old_time))

        f2 = conv_dir / "new.json"
        f2.write_text('{"text": "new record here"}')
        # Current mtime (now)

        result = analyze_vault(tmp_path)

        assert result["sampled_days"] >= 2
        assert result["growth_rate_bytes_per_day"] > 0
        assert result["projected_30d_bytes"] > result["current_bytes"]

    def test_projection_uses_growth_rate(self, tmp_path):
        """30-day projection equals current + (rate * 30)."""
        conv_dir = tmp_path / "memory" / "conversation"
        conv_dir.mkdir(parents=True)

        import os
        import time

        f1 = conv_dir / "a.json"
        f1.write_text('{"x": 1}')
        old_time = time.time() - (5 * 86400)
        os.utime(f1, (old_time, old_time))

        f2 = conv_dir / "b.json"
        f2.write_text('{"x": 2}')

        result = analyze_vault(tmp_path)

        expected = result["current_bytes"] + (result["growth_rate_bytes_per_day"] * 30)
        assert result["projected_30d_bytes"] == expected

    def test_human_readable_fields_present(self, tmp_path):
        """All human-readable fields are strings."""
        conv_dir = tmp_path / "memory" / "conversation"
        conv_dir.mkdir(parents=True)
        (conv_dir / "a.json").write_text('{"text": "data"}')

        result = analyze_vault(tmp_path)

        assert isinstance(result["current_human"], str)
        assert isinstance(result["projected_30d_human"], str)
        for entry in result["by_type"].values():
            assert isinstance(entry["human"], str)
