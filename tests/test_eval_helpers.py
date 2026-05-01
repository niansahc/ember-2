"""
tests/test_eval_helpers.py

Tests for eval helper functions: test vault isolation and cleanup.

The vault swap helpers must call POST /v1/developer/vault/swap on the
running API process. Setting only an in-process global (the prior
implementation) does not reach the API and silently leaks the live
vault into evals. These tests are the contract for that fix.
"""

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
)


def _make_response(status_code: int = 200, payload: dict | None = None) -> MagicMock:
    """Build a MagicMock that mimics httpx.Response for our call sites."""
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload or {"label": "test", "active_vault": "/tmp/test"}
    if status_code >= 400:
        response.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        response.raise_for_status.return_value = None
    return response


class TestSwapToTestVault:

    def test_swap_exits_when_not_configured(self, tmp_path, monkeypatch):
        """No VAULT_PATH_TEST set: must fail closed with SystemExit, not
        silently fall through. The original 2026-04-30 leak was a silent
        return None when env var was absent in the eval subprocess."""
        monkeypatch.delenv("VAULT_PATH_TEST", raising=False)
        with patch("tools.eval_helpers.httpx.post") as mock_post:
            from tools.eval_helpers import swap_to_test_vault
            with pytest.raises(SystemExit) as exc_info:
                swap_to_test_vault()
            assert exc_info.value.code == 1
        assert mock_post.call_count == 0

    def test_swap_exits_when_dir_missing(self, tmp_path, monkeypatch):
        """VAULT_PATH_TEST points at nonexistent dir: must fail closed
        with SystemExit. Same privacy reasoning as the unset-env case."""
        missing = str(tmp_path / "nonexistent")
        monkeypatch.setenv("VAULT_PATH_TEST", missing)
        with patch("tools.eval_helpers.httpx.post") as mock_post:
            from tools.eval_helpers import swap_to_test_vault
            with pytest.raises(SystemExit) as exc_info:
                swap_to_test_vault()
            assert exc_info.value.code == 1
        assert mock_post.call_count == 0

    def test_swap_calls_api_with_test_label(self, tmp_path, monkeypatch):
        """Privacy contract: swap_to_test_vault must POST to the API
        with vault_label=test. Setting an in-process global only is the
        bug this test guards against."""
        test_vault = tmp_path / "test_vault"
        test_vault.mkdir()
        monkeypatch.setenv("VAULT_PATH_TEST", str(test_vault))

        with patch("tools.eval_helpers.httpx.post", return_value=_make_response()) as mock_post, \
             patch("src.core.config.get_ember_api_key", return_value="test-api-key-123"):
            from tools.eval_helpers import swap_to_test_vault
            prev = swap_to_test_vault()

        assert prev == "test"
        assert mock_post.call_count == 1

        call = mock_post.call_args
        url = call.args[0] if call.args else call.kwargs.get("url")
        assert url.endswith("/v1/developer/vault/swap")
        assert call.kwargs["json"] == {"vault_label": "test"}
        headers = call.kwargs["headers"]
        assert headers["Authorization"] == "Bearer test-api-key-123"
        assert headers["Content-Type"] == "application/json"

    def test_swap_omits_authorization_header_when_no_api_key(self, tmp_path, monkeypatch):
        """When EMBER_API_KEY is not configured, the Authorization
        header is omitted (open access)."""
        test_vault = tmp_path / "test_vault"
        test_vault.mkdir()
        monkeypatch.setenv("VAULT_PATH_TEST", str(test_vault))

        with patch("tools.eval_helpers.httpx.post", return_value=_make_response()) as mock_post, \
             patch("src.core.config.get_ember_api_key", return_value=""):
            from tools.eval_helpers import swap_to_test_vault
            swap_to_test_vault()

        call = mock_post.call_args
        headers = call.kwargs["headers"]
        assert "Authorization" not in headers

    def test_swap_exits_on_api_failure(self, tmp_path, monkeypatch):
        """Privacy regression guard: when the swap call fails for any
        reason (connection refused, 403 dev-mode off, 5xx), the helper
        must exit the process. Falling through silently is the bug."""
        test_vault = tmp_path / "test_vault"
        test_vault.mkdir()
        monkeypatch.setenv("VAULT_PATH_TEST", str(test_vault))

        def _raise_connection_error(*_args, **_kwargs):
            raise ConnectionError("connection refused")

        with patch("tools.eval_helpers.httpx.post", side_effect=_raise_connection_error), \
             patch("src.core.config.get_ember_api_key", return_value=""):
            from tools.eval_helpers import swap_to_test_vault
            with pytest.raises(SystemExit) as exc_info:
                swap_to_test_vault()
            assert exc_info.value.code == 1

    def test_swap_exits_on_403_dev_mode_disabled(self, tmp_path, monkeypatch):
        """If EMBER_DEV_MODE is not set on the API, the endpoint returns
        403. Helper must exit, not fall through."""
        test_vault = tmp_path / "test_vault"
        test_vault.mkdir()
        monkeypatch.setenv("VAULT_PATH_TEST", str(test_vault))

        with patch(
            "tools.eval_helpers.httpx.post",
            return_value=_make_response(status_code=403, payload={"detail": "dev mode required"}),
        ), patch("src.core.config.get_ember_api_key", return_value=""):
            from tools.eval_helpers import swap_to_test_vault
            with pytest.raises(SystemExit) as exc_info:
                swap_to_test_vault()
            assert exc_info.value.code == 1

    def test_swap_uses_ember_api_base_override(self, tmp_path, monkeypatch):
        """EMBER_API_BASE env var overrides the localhost:8000 default."""
        test_vault = tmp_path / "test_vault"
        test_vault.mkdir()
        monkeypatch.setenv("VAULT_PATH_TEST", str(test_vault))
        monkeypatch.setenv("EMBER_API_BASE", "http://localhost:9999")

        with patch("tools.eval_helpers.httpx.post", return_value=_make_response()) as mock_post, \
             patch("src.core.config.get_ember_api_key", return_value=""):
            from tools.eval_helpers import swap_to_test_vault
            swap_to_test_vault()

        call = mock_post.call_args
        url = call.args[0] if call.args else call.kwargs.get("url")
        assert url == "http://localhost:9999/v1/developer/vault/swap"


class TestRestoreVault:

    def test_restore_calls_api_with_default_label(self, monkeypatch):
        """Restore must POST vault_label=default to revert the API
        process to PRIVATE_VAULT_PATH."""
        with patch("tools.eval_helpers.httpx.post", return_value=_make_response()) as mock_post, \
             patch("src.core.config.get_ember_api_key", return_value="test-api-key-123"):
            from tools.eval_helpers import restore_vault
            restore_vault("test")

        assert mock_post.call_count == 1
        call = mock_post.call_args
        url = call.args[0] if call.args else call.kwargs.get("url")
        assert url.endswith("/v1/developer/vault/swap")
        assert call.kwargs["json"] == {"vault_label": "default"}

    def test_restore_with_none_skips_api_call(self):
        """When swap was skipped (None returned), restore must also skip
        and not fire a stray API call."""
        with patch("tools.eval_helpers.httpx.post") as mock_post:
            from tools.eval_helpers import restore_vault
            restore_vault(None)
        assert mock_post.call_count == 0

    def test_restore_swallows_failure_does_not_exit(self):
        """Restore is best-effort: a failed restore call must not exit
        or raise. Eval is already done; manual restore is documented."""
        def _raise(*_args, **_kwargs):
            raise Exception("boom")

        with patch("tools.eval_helpers.httpx.post", side_effect=_raise), \
             patch("src.core.config.get_ember_api_key", return_value=""):
            from tools.eval_helpers import restore_vault
            # Must not raise, must not exit
            restore_vault("test")


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

    def test_eval_local_models_swaps_vault(self):
        """eval_local_models was simplified to use the shared helpers
        rather than inline API calls. This guards against a regression
        back to inline calls."""
        source = (REPO_ROOT / "tools" / "eval_local_models.py").read_text(encoding="utf-8")
        assert "swap_to_test_vault" in source
        assert "restore_vault" in source
