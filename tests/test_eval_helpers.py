"""
tests/test_eval_helpers.py

Tests for eval helper functions: test vault isolation, and no post-run
cleanup in the eval process.

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
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Imported before any test patches the vault resolver: the script binds it
# at import time (see _RESOLVER_BINDING_MODULES in tests/conftest.py).
from cleanup_test_artifacts import archive_records, scan_vault


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


class TestNoPostRunCleanup:
    """An eval run never resolves the vault in its own process.

    swap_to_test_vault() moves only the API process. The eval process has
    no override, so get_private_vault_path() there resolves
    PRIVATE_VAULT_PATH, the live vault: the post-run cleanup the eval tools
    used to run scanned live records and archived the ones it matched.

    These tests run eval_web_search end to end against a faked API. The
    resolver records each call and returns a stand-in live vault holding a
    record that cleanup matched (April 2026, source "eval").
    """

    def _run_eval(self, monkeypatch, tmp_path, cleanup_before_restore=False):
        import httpx
        import src.core.config as config
        import tools.eval_helpers as eval_helpers
        import tools.eval_web_search as eval_web_search

        live = tmp_path / "live_vault"
        record_path = live / "memory" / "conversation" / "2026-04-12T10-00-00.json"
        record_path.parent.mkdir(parents=True)
        record_path.write_text(json.dumps({
            "id": "2026-04-12T10-00-00",
            "timestamp": "2026-04-12T10-00-00",
            "type": "conversation",
            "text": "A synthetic record.",
            "source": "eval",
            "tags": ["test"],
        }), encoding="utf-8")
        test_vault = tmp_path / "test_vault"
        test_vault.mkdir()
        monkeypatch.setenv("VAULT_PATH_TEST", str(test_vault))

        resolved = []

        def _resolve_live_vault():
            resolved.append(live)
            return live

        monkeypatch.setattr(config, "get_private_vault_path", _resolve_live_vault)

        def _post(url, **kwargs):
            if url.endswith("/v1/developer/vault/swap"):
                return _make_response(payload={"label": kwargs["json"]["vault_label"]})
            response = _make_response(
                payload={"choices": [{"message": {"content": "A synthetic answer."}}]}
            )
            response.headers = {"X-Ember-Web-Search": "true"}
            return response

        monkeypatch.setattr(httpx, "post", _post)
        monkeypatch.setattr(
            httpx, "get", lambda url, **kwargs: _make_response(payload={"model": "test-model"})
        )
        monkeypatch.setattr(config, "get_ember_api_key", lambda: "test-api-key")
        monkeypatch.setattr(eval_web_search, "REPO_ROOT", tmp_path)
        monkeypatch.setattr(eval_web_search, "TEST_QUESTIONS", eval_web_search.TEST_QUESTIONS[:1])
        monkeypatch.setattr(sys, "argv", ["eval_web_search.py"])

        if cleanup_before_restore:
            # The step the eval tools ran before restoring the vault.
            restore = eval_helpers.restore_vault

            def _cleanup_then_restore(previous):
                vault = config.get_private_vault_path()
                archive_records(vault, scan_vault(vault))
                restore(previous)

            monkeypatch.setattr(eval_helpers, "restore_vault", _cleanup_then_restore)

        eval_web_search.main()
        return resolved, record_path, live

    def test_web_search_eval_never_resolves_the_vault(self, monkeypatch, tmp_path):
        resolved, record_path, live = self._run_eval(monkeypatch, tmp_path)

        assert resolved == []
        assert record_path.exists()
        assert not (live / "memory" / "archive").exists()

    def test_control_a_post_run_cleanup_resolves_and_archives_the_live_vault(
        self, monkeypatch, tmp_path
    ):
        """Positive control: the same run with the old cleanup step put back
        resolves the stand-in live vault and moves its record to the
        archive, so the assertions above can fail."""
        resolved, record_path, live = self._run_eval(
            monkeypatch, tmp_path, cleanup_before_restore=True
        )

        assert resolved == [live]
        assert not record_path.exists()
        assert (live / "memory" / "archive" / "conversation__2026-04-12T10-00-00.json").exists()


class TestEvalToolsHaveNoPostRunCleanup:
    """The eval tools end a run by restoring the API's vault, nothing more."""

    def test_eval_tools_do_not_call_a_cleanup(self):
        import tools.eval_helpers as eval_helpers

        assert not hasattr(eval_helpers, "run_cleanup")
        # Control: this is the module the eval tools import their helpers from.
        assert hasattr(eval_helpers, "restore_vault")
        for name in ("eval_manual.py", "eval_web_search.py"):
            source = (REPO_ROOT / "tools" / name).read_text(encoding="utf-8")
            assert "run_cleanup" not in source, name
            # Control: a helper call the tool does make is visible to this check.
            assert "restore_vault(previous_vault)" in source, name

    def test_manual_mode_has_no_cleanup_prompt(self):
        source = (REPO_ROOT / "tools" / "eval_manual.py").read_text(encoding="utf-8")
        assert "Clean up eval artifacts from vault?" not in source
        # Control: manual mode's remaining prompt is in the text searched.
        assert "Save to eval_history.md? (y/n)" in source


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

    def test_eval_conversations_swaps_vault(self):
        """Issue #146: eval_conversations.py ships response text to a
        cloud judge and must isolate to the test vault first."""
        source = (REPO_ROOT / "tools" / "eval_conversations.py").read_text(encoding="utf-8")
        assert "swap_to_test_vault" in source
        assert "restore_vault" in source

    def test_eval_probe_swaps_vault_standalone(self):
        """eval_probe.py's standalone main() has no caller to inherit
        isolation from (unlike the eval_manual.py --probe path)."""
        source = (REPO_ROOT / "tools" / "eval_probe.py").read_text(encoding="utf-8")
        assert "swap_to_test_vault" in source
        assert "restore_vault" in source


class TestModelPinning:
    """Verify that all three model-switching eval tools delegate to
    pin_model (ADR-043) rather than posting a persisting swap inline.

    A persisting POST /model writes model_override.json, which routes
    the intent classifier, coaching filter, deviation detector and
    reflection paths onto the candidate too, and clobbers the user's
    persisted model choice mid-sweep. Guards against regression back to
    an inline swap."""

    def test_eval_local_models_pins(self):
        source = (REPO_ROOT / "tools" / "eval_local_models.py").read_text(encoding="utf-8")
        assert "pin_model" in source

    def test_eval_conversations_pins(self):
        source = (REPO_ROOT / "tools" / "eval_conversations.py").read_text(encoding="utf-8")
        assert "pin_model" in source

    def test_eval_manual_pins(self):
        source = (REPO_ROOT / "tools" / "eval_manual.py").read_text(encoding="utf-8")
        assert "pin_model" in source
