"""
tests/test_guard_fail_closed.py

The vault guards (vault_guard.py, secret_dump_guard.py) must deny, never
crash. A hook that exits non-zero is a non-blocking error to the harness,
which then runs the tool call, so a crash is an allow.

Two ways in:
  1. A foreign top-level `src` package earlier on sys.path. The guards used
     to `import src.core.vault_path`, which that package shadowed; the import
     raised and the read of the configured vault went through. The resolver
     is now loaded by file path from the hook's own checkout.
  2. Any other exception inside the check: the guard emits a deny with the
     exception type only (its message can carry the vault path).

Everything runs under tmp_path; no env file or real vault is read.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import hook_output, hook_payload, load_hook, make_vault_dirs, run_hook

RELATIVE = "vaults/live"


@pytest.fixture
def shadowed(hermetic_hook_project: Path, tmp_path: Path, monkeypatch):
    """A tmp project whose configured vault is vaults/live, and a PYTHONPATH
    entry holding a foreign top-level `src` package."""
    make_vault_dirs(hermetic_hook_project)
    shadow = tmp_path / "shadow"
    (shadow / "src").mkdir(parents=True)
    (shadow / "src" / "__init__.py").write_text("", encoding="utf-8")
    monkeypatch.setenv("PRIVATE_VAULT_PATH", RELATIVE)
    monkeypatch.setenv("PYTHONPATH", str(shadow))
    return hermetic_hook_project, shadow


def test_shadow_package_is_what_a_hook_process_imports(shadowed):
    """Control: the fixture really shadows `src` in a hook-like process, so
    the deny tests below exercise the case that used to fail open."""
    project, shadow = shadowed
    # cwd away from the repo: `python -c` puts the cwd first on sys.path, the
    # way a hook script puts its own directory there (which has no src/).
    out = subprocess.run(
        [sys.executable, "-c", "import src, os; print(os.path.dirname(src.__file__))"],
        capture_output=True, text=True, check=True, cwd=project,
    ).stdout.strip()
    assert Path(out).resolve() == (shadow / "src").resolve()


def test_vault_guard_denies_configured_vault_read_with_src_shadowed(shadowed):
    project, _ = shadowed
    target = project / "vaults" / "live" / "x.json"
    result = run_hook("vault_guard.py", hook_payload("Read", cwd=str(project), file_path=str(target)))
    assert result.returncode == 0, result.stderr
    output = hook_output(result)
    assert output is not None and output["permissionDecision"] == "deny"
    assert "matched" in output["permissionDecisionReason"]


def test_secret_dump_guard_denies_cat_in_configured_vault_with_src_shadowed(shadowed):
    project, _ = shadowed
    target = project / "vaults" / "live" / "x.json"
    payload = {"tool_name": "Bash", "tool_input": {"command": f'cat "{target}"'}, "cwd": str(project)}
    result = run_hook("secret_dump_guard.py", payload)
    assert result.returncode == 0, result.stderr
    output = hook_output(result)
    assert output is not None and output["permissionDecision"] == "deny"
    assert "'cat'" in output["permissionDecisionReason"]


# ---------------------------------------------------------------------------
# Any internal error denies (in-process, so the failure can be forced)
# ---------------------------------------------------------------------------

SECRET_IN_MESSAGE = "C:/synthetic/vault/path-must-not-print"


@pytest.fixture
def broken_resolver(hermetic_hook_project: Path, monkeypatch):
    """The shared resolver raises, with a path in its message."""
    make_vault_dirs(hermetic_hook_project)
    monkeypatch.setenv("PRIVATE_VAULT_PATH", RELATIVE)
    load_hook("vault_guard.py")  # puts the hooks dir on sys.path
    import hook_utils

    def _raise():
        raise RuntimeError(SECRET_IN_MESSAGE)

    monkeypatch.setattr(hook_utils, "_vault_resolver", _raise)
    return hermetic_hook_project


def _run_main(module, payload: dict, monkeypatch, capsys) -> dict | None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    module.main()  # must not raise
    out = capsys.readouterr().out.strip()
    return json.loads(out)["hookSpecificOutput"] if out else None


def test_vault_guard_denies_when_its_check_raises(broken_resolver, monkeypatch, capsys):
    target = broken_resolver / "src" / "a.py"
    output = _run_main(
        load_hook("vault_guard.py"),
        hook_payload("Read", cwd=str(broken_resolver), file_path=str(target)),
        monkeypatch, capsys,
    )
    assert output is not None and output["permissionDecision"] == "deny"
    assert "RuntimeError" in output["permissionDecisionReason"]
    assert SECRET_IN_MESSAGE not in output["permissionDecisionReason"]


def test_secret_dump_guard_denies_when_its_check_raises(broken_resolver, monkeypatch, capsys):
    target = broken_resolver / "src" / "a.py"
    payload = {"tool_name": "Bash", "tool_input": {"command": f'cat "{target}"'}, "cwd": str(broken_resolver)}
    output = _run_main(load_hook("secret_dump_guard.py"), payload, monkeypatch, capsys)
    assert output is not None and output["permissionDecision"] == "deny"
    assert "RuntimeError" in output["permissionDecisionReason"]
    assert SECRET_IN_MESSAGE not in output["permissionDecisionReason"]


def test_guards_allow_the_same_calls_when_the_check_works(hermetic_hook_project, monkeypatch, capsys):
    """Positive control for the two tests above: with a working resolver the
    same non-vault calls are allowed, so the denies come from the error."""
    make_vault_dirs(hermetic_hook_project)
    monkeypatch.setenv("PRIVATE_VAULT_PATH", RELATIVE)
    target = hermetic_hook_project / "src" / "a.py"
    read = hook_payload("Read", cwd=str(hermetic_hook_project), file_path=str(target))
    cat = {"tool_name": "Bash", "tool_input": {"command": f'cat "{target}"'}, "cwd": str(hermetic_hook_project)}
    assert _run_main(load_hook("vault_guard.py"), read, monkeypatch, capsys) is None
    assert _run_main(load_hook("secret_dump_guard.py"), cat, monkeypatch, capsys) is None


@pytest.mark.parametrize("payload", ([], {"tool_input": "x"}, {"tool_input": {"command": 5}}))
def test_secret_dump_guard_allows_an_unusable_payload(payload, monkeypatch, capsys):
    """An unusable payload is allowed through, as an unparseable one is; it
    is not an internal error."""
    assert _run_main(load_hook("secret_dump_guard.py"), payload, monkeypatch, capsys) is None
