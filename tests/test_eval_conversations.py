"""tests/test_eval_conversations.py

Vault isolation contract for tools/eval_conversations.py (issue #146).

eval_conversations.py drives the local API and ships response text to a
cloud Claude judge. If the API is serving the live vault, vault-grounded
response text leaves the machine. These tests confirm the eval isolates
to the test vault before any request goes out, and fails closed
(exits, sends nothing) when the swap cannot be established.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _stub_anthropic(monkeypatch):
    """Inject a fake anthropic module so `import anthropic` succeeds
    without the real package installed in the test environment."""
    monkeypatch.setitem(sys.modules, "anthropic", MagicMock())


class TestVaultIsolation:

    def test_main_exits_before_any_request_when_swap_fails(self, monkeypatch):
        """swap_to_test_vault() exits 1 on failure. main() must not send
        a single request to Ember or the cloud judge in that case."""
        _stub_anthropic(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        monkeypatch.setattr(sys, "argv", ["eval_conversations.py"])

        import tools.eval_conversations as ec

        with patch("tools.eval_conversations.swap_to_test_vault", side_effect=SystemExit(1)) as mock_swap, \
             patch("tools.eval_conversations.httpx.post") as mock_post:
            with pytest.raises(SystemExit) as exc_info:
                ec.main()
            assert exc_info.value.code == 1

        mock_swap.assert_called_once()
        mock_post.assert_not_called()

    def test_main_swaps_to_test_vault_before_run_eval(self, monkeypatch, tmp_path):
        """On a successful swap, main() must call swap_to_test_vault
        before run_eval sends any request, and restore_vault afterward."""
        _stub_anthropic(monkeypatch)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key")
        monkeypatch.setattr(sys, "argv", ["eval_conversations.py"])

        import tools.eval_conversations as ec
        monkeypatch.setattr(ec, "REPO_ROOT", tmp_path)

        call_order: list[str] = []

        def _fake_swap():
            call_order.append("swap")
            return "test"

        def _fake_run_eval(verbose=False):
            call_order.append("run_eval")
            return [], "summary"

        def _fake_restore(previous):
            call_order.append("restore")

        with patch("tools.eval_conversations.swap_to_test_vault", side_effect=_fake_swap), \
             patch("tools.eval_conversations.restore_vault", side_effect=_fake_restore), \
             patch("tools.eval_conversations.run_eval", side_effect=_fake_run_eval):
            ec.main()

        assert call_order == ["swap", "run_eval", "restore"]

    def test_source_imports_swap_and_restore(self):
        source = (REPO_ROOT / "tools" / "eval_conversations.py").read_text(encoding="utf-8")
        assert "swap_to_test_vault" in source
        assert "restore_vault" in source
