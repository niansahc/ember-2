"""
tests/test_eval_retrieval.py

tools/eval_retrieval.py resolves the vault in its own process (it builds
context packets in-process), so run directly it must pin itself to the test
vault or refuse, and its --verbose content previews must never reach logs/.

Synthetic tmp vaults and a fake ContextService only; no test here builds a
packet from a real vault.
"""

import os
import sys

import pytest

import tools.eval_retrieval as eval_retrieval
from src.context.models import ContextItem, ContextPacket
from tests.conftest import pinned_vault, set_eval_vaults

# Synthetic retrieved content. If it reaches a file under logs/, a verbose
# run wrote vault content to disk.
MARKER = "SYNTHETIC_PREVIEW_MARKER_retrieved_record_body_for_the_verbose_test"


def _run_main(tmp_path, monkeypatch, argv=("eval_retrieval.py",), fake_eval=True):
    """Run main() with logs under tmp_path; return (exit code, calls).
    With fake_eval, run_eval is faked out too; run_integrity_eval always is."""
    calls = []
    monkeypatch.setattr(eval_retrieval, "REPO_ROOT", tmp_path)
    if fake_eval:
        monkeypatch.setattr(
            eval_retrieval, "run_eval",
            lambda verbose=False: calls.append("run_eval") or ([], "SUMMARY"),
        )
    monkeypatch.setattr(
        eval_retrieval, "run_integrity_eval",
        lambda: calls.append("run_integrity_eval") or ([], "INTEGRITY"),
    )
    monkeypatch.setattr(sys, "argv", list(argv))
    try:
        eval_retrieval.main()
        code = 0
    except SystemExit as exc:
        code = exc.code
    return code, calls


def _log_files(tmp_path):
    logs = tmp_path / "logs"
    return [p for p in logs.rglob("*") if p.is_file()] if logs.exists() else []


class TestTestVaultOnly:

    @pytest.mark.parametrize("test_vault", [None, "live_vault"], ids=["unset", "same-path"])
    def test_refuses_before_running(self, tmp_path, monkeypatch, test_vault):
        live = set_eval_vaults(
            tmp_path, monkeypatch, None if test_vault is None else tmp_path / test_vault,
        )
        code, calls = _run_main(tmp_path, monkeypatch)
        assert code == 1
        assert calls == []
        assert _log_files(tmp_path) == []
        assert os.environ["PRIVATE_VAULT_PATH"] == str(live)

    def test_control_runs_on_the_pinned_test_vault(self, tmp_path, monkeypatch):
        """Positive control for the refusals: with a distinct test vault the
        eval runs, writes its log, and the process resolves the test vault,
        so the calls/log/env assertions above can fail."""
        test_vault = tmp_path / "test_vault"
        test_vault.mkdir()
        set_eval_vaults(tmp_path, monkeypatch, test_vault)
        code, calls = _run_main(tmp_path, monkeypatch)
        assert code == 0
        assert calls == ["run_eval", "run_integrity_eval"]
        assert list((tmp_path / "logs" / "retrieval_eval").glob("eval_*.log"))
        assert pinned_vault() == test_vault.resolve()

    def test_ablation_resolves_its_own_test_vault(self, tmp_path, monkeypatch):
        """--ablation is dispatched before the pin: the ablation runner
        applies the same refusal itself, and would refuse a process whose
        PRIVATE_VAULT_PATH had already been pinned to VAULT_PATH_TEST."""
        live = set_eval_vaults(tmp_path, monkeypatch, None)
        dispatched = []
        monkeypatch.setattr(
            eval_retrieval, "run_ablation",
            lambda verbose=False: dispatched.append(verbose) or 0,
        )
        code, calls = _run_main(
            tmp_path, monkeypatch, argv=("eval_retrieval.py", "--ablation"),
        )
        assert code == 0
        assert dispatched == [False]
        assert calls == []
        assert os.environ["PRIVATE_VAULT_PATH"] == str(live)


class _FakeContextService:
    """Returns one packet whose top item carries MARKER as its content."""

    def build_context(self, query, read_only=False):
        return ContextPacket(
            user_message=query,
            memory_items=[ContextItem(
                id="synth-1", content=MARKER, source="journal",
                item_type="journal", memory_type="journal", score=0.9,
            )],
        )


def test_verbose_run_writes_no_preview_to_logs(tmp_path, monkeypatch, capsys):
    """The marker on stdout is the positive control: the preview ran and
    had the content. It must not be in any file main() writes."""
    test_vault = tmp_path / "test_vault"
    test_vault.mkdir()
    set_eval_vaults(tmp_path, monkeypatch, test_vault)
    monkeypatch.setattr(eval_retrieval, "ContextService", _FakeContextService)
    monkeypatch.setattr(eval_retrieval, "EVAL_CASES", eval_retrieval.EVAL_CASES[:1])

    code, _ = _run_main(
        tmp_path, monkeypatch, argv=("eval_retrieval.py", "--verbose"), fake_eval=False,
    )

    written = _log_files(tmp_path)
    assert code == 0
    assert written, "control: the run must have written its log files"
    assert MARKER in capsys.readouterr().out
    for path in written:
        assert MARKER not in path.read_text(encoding="utf-8")
