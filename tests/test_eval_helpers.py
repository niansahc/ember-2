"""
tests/test_eval_helpers.py

Tests for eval helper functions: test vault isolation and cleanup.
"""

import os
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.core.config import (
    set_vault_path_override,
    clear_vault_path_override,
    get_vault_label,
)


class TestSwapToTestVault:

    def setup_method(self):
        clear_vault_path_override()

    def teardown_method(self):
        clear_vault_path_override()

    def test_swap_sets_override_when_test_vault_exists(self, tmp_path):
        test_vault = tmp_path / "test_vault"
        test_vault.mkdir()
        with patch.dict(os.environ, {"VAULT_PATH_TEST": str(test_vault)}):
            from tools.eval_helpers import swap_to_test_vault
            prev = swap_to_test_vault()
        assert prev is not None
        assert get_vault_label() == "test"
        clear_vault_path_override()

    def test_swap_returns_none_when_not_configured(self):
        with patch.dict(os.environ, {}, clear=True):
            env = dict(os.environ)
            env.pop("VAULT_PATH_TEST", None)
            with patch.dict(os.environ, env, clear=True):
                from tools.eval_helpers import swap_to_test_vault
                prev = swap_to_test_vault()
        assert prev is None

    def test_swap_returns_none_when_dir_missing(self, tmp_path):
        missing = str(tmp_path / "nonexistent")
        with patch.dict(os.environ, {"VAULT_PATH_TEST": missing}):
            from tools.eval_helpers import swap_to_test_vault
            prev = swap_to_test_vault()
        assert prev is None


class TestRestoreVault:

    def setup_method(self):
        clear_vault_path_override()

    def teardown_method(self):
        clear_vault_path_override()

    def test_restore_clears_override(self):
        set_vault_path_override("/test/path", "test")
        from tools.eval_helpers import restore_vault
        restore_vault("/original/path")
        assert get_vault_label() == "default"

    def test_restore_with_none_clears(self):
        set_vault_path_override("/test/path", "test")
        from tools.eval_helpers import restore_vault
        restore_vault(None)
        assert get_vault_label() == "default"


class TestRunCleanup:

    def test_cleanup_succeeds_on_empty_vault(self, tmp_path):
        """Cleanup on a vault with no matching records should succeed silently."""
        vault = tmp_path / "vault"
        (vault / "memory" / "conversation").mkdir(parents=True)
        set_vault_path_override(str(vault), "test")
        try:
            from tools.eval_helpers import run_cleanup
            run_cleanup()  # should not raise
        finally:
            clear_vault_path_override()

    def test_cleanup_archives_matching_records(self, tmp_path):
        """Cleanup should archive test artifacts."""
        vault = tmp_path / "vault"
        conv_dir = vault / "memory" / "conversation"
        conv_dir.mkdir(parents=True)
        (vault / "memory" / "archive").mkdir(parents=True)

        # Write a test artifact
        record = {
            "id": "2026-04-12T10-00-00",
            "timestamp": "2026-04-12T10-00-00",
            "type": "conversation",
            "text": "What do you know about me?",
            "source": "eval",
            "tags": ["test"],
        }
        (conv_dir / "2026-04-12T10-00-00.json").write_text(
            json.dumps(record), encoding="utf-8"
        )

        set_vault_path_override(str(vault), "test")
        try:
            from tools.eval_helpers import run_cleanup
            run_cleanup()
            # Original should be moved
            assert not (conv_dir / "2026-04-12T10-00-00.json").exists()
            # Archive should have it
            archive_dir = vault / "memory" / "archive"
            assert len(list(archive_dir.glob("*.json"))) == 1
        finally:
            clear_vault_path_override()


class TestEvalManualCleanupPrompt:
    """Verify that manual mode has the cleanup prompt string in the source."""

    def test_cleanup_prompt_in_manual_mode(self):
        source = (REPO_ROOT / "tools" / "eval_manual.py").read_text(encoding="utf-8")
        assert "Clean up eval artifacts from vault?" in source
        assert "run_cleanup" in source

    def test_auto_mode_calls_cleanup_silently(self):
        source = (REPO_ROOT / "tools" / "eval_manual.py").read_text(encoding="utf-8")
        # Auto mode should call run_cleanup() without prompting
        assert "run_cleanup()" in source

    def test_web_search_eval_calls_cleanup(self):
        source = (REPO_ROOT / "tools" / "eval_web_search.py").read_text(encoding="utf-8")
        assert "run_cleanup()" in source


class TestVaultIsolation:
    """Verify that both eval tools call swap_to_test_vault."""

    def test_eval_manual_swaps_vault(self):
        source = (REPO_ROOT / "tools" / "eval_manual.py").read_text(encoding="utf-8")
        assert "swap_to_test_vault" in source
        assert "restore_vault" in source

    def test_eval_web_search_swaps_vault(self):
        source = (REPO_ROOT / "tools" / "eval_web_search.py").read_text(encoding="utf-8")
        assert "swap_to_test_vault" in source
        assert "restore_vault" in source
