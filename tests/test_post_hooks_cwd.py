"""
tests/test_post_hooks_cwd.py

The PostToolUse hooks (.claude/hooks/post_edit_test.py,
.claude/hooks/post_commit_eval.py) must run against the project root
whichever directory the session has cd'd into. Each test runs the real
hook process from an unrelated tmp cwd, with CLAUDE_PROJECT_DIR pointing
at a synthetic tmp project, and checks the hook's subprocess ran there.

No test touches the developer's checkout, its .env, or any vault: the
synthetic project carries its own trivial test file, a stub
tools/eval_retrieval.py, and empty tmp vault directories.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import load_hook, run_hook

REPO_ROOT = Path(__file__).resolve().parents[1]
POST_EDIT = "post_edit_test.py"
POST_COMMIT = "post_commit_eval.py"


@pytest.fixture
def elsewhere(tmp_path) -> Path:
    path = tmp_path / "elsewhere" / "nested"
    path.mkdir(parents=True)
    return path


def test_post_edit_runs_project_tests_from_another_cwd(tmp_path, elsewhere):
    project = tmp_path / "proj"
    (project / "tests").mkdir(parents=True)
    (project / "tests" / "test_marker.py").write_text(
        "def test_marker():\n    pass\n", encoding="utf-8"
    )
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(project / "src" / "x.py")}}
    result = run_hook(POST_EDIT, payload, cwd=elsewhere, env={"CLAUDE_PROJECT_DIR": str(project)})
    assert result.returncode == 0
    assert "1 passed" in result.stdout, result.stdout


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git for a synthetic repo")
def test_post_commit_runs_eval_in_project_from_another_cwd(tmp_path, elsewhere):
    project = tmp_path / "proj"
    (project / "src" / "context").mkdir(parents=True)
    (project / "tools").mkdir()
    (project / "tools" / "eval_retrieval.py").write_text(
        "import os\nprint('EVAL STUB cwd=' + os.getcwd())\n", encoding="utf-8"
    )
    git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], cwd=project, check=True)
    (project / "src" / "context" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run([*git, "add", "-A"], cwd=project, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "touch context"], cwd=project, check=True)

    test_vault = tmp_path / "test_vault"
    test_vault.mkdir()
    env = {
        "CLAUDE_PROJECT_DIR": str(project),
        "VAULT_PATH_TEST": str(test_vault),
        "PRIVATE_VAULT_PATH": str(tmp_path / "live_vault_placeholder"),
    }
    payload = {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "x"'}}
    result = run_hook(POST_COMMIT, payload, cwd=elsewhere, env=env)
    assert result.returncode == 0
    assert f"EVAL STUB cwd={project}" in result.stdout, result.stdout


@pytest.mark.parametrize("hook", [POST_EDIT, POST_COMMIT])
def test_repo_root_falls_back_to_hook_location(hook, monkeypatch):
    """Without CLAUDE_PROJECT_DIR (a manual run), the anchor is the
    checkout the hook file lives in, not the cwd."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert load_hook(hook).REPO_ROOT == REPO_ROOT


# ---------------------------------------------------------------------------
# Relative vault paths resolve against the project root (as vault_guard.py
# resolves them), never against the process cwd. The unrelated cwd holds
# same-named directories, so a cwd-relative resolution finds a real (wrong)
# directory instead of failing on a missing one.
# ---------------------------------------------------------------------------

def _make_vault_dirs(*roots: Path) -> None:
    for root in roots:
        (root / "vaults" / "test").mkdir(parents=True)
        (root / "vaults" / "live").mkdir(parents=True)


def _resolve_in(project: Path, cwd: Path, monkeypatch, **values: str):
    """post_commit_eval._resolve_test_vault() with CLAUDE_PROJECT_DIR at
    `project`, the process cwd at `cwd`, and only the given values set."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    for key in ("VAULT_PATH_TEST", "PRIVATE_VAULT_PATH"):
        if key in values:
            monkeypatch.setenv(key, values[key])
        else:
            monkeypatch.delenv(key, raising=False)
    hook = load_hook(POST_COMMIT)
    # The tmp project has no env file, so an unset key stays unset.
    assert not hook.ENV_PATH.exists()
    monkeypatch.chdir(cwd)
    return hook._resolve_test_vault()


@pytest.fixture
def rel_project(tmp_path, elsewhere) -> Path:
    project = tmp_path / "proj_rel"
    _make_vault_dirs(project, elsewhere)
    return project


def test_relative_test_vault_resolves_against_project_root(rel_project, elsewhere, monkeypatch):
    vault, reason = _resolve_in(
        rel_project, elsewhere, monkeypatch,
        VAULT_PATH_TEST="vaults/test", PRIVATE_VAULT_PATH="vaults/live",
    )
    assert reason is None
    assert vault == (rel_project / "vaults" / "test").resolve()


def test_relative_live_vault_equal_to_test_vault_is_refused(rel_project, elsewhere, monkeypatch):
    """Positive control for the test above: the live vault given relative,
    the test vault absolute, both naming one project directory. A
    cwd-relative resolution puts the live vault under `elsewhere`, sees two
    different paths, and runs the eval against the live vault."""
    vault, reason = _resolve_in(
        rel_project, elsewhere, monkeypatch,
        VAULT_PATH_TEST=str(rel_project / "vaults" / "live"),
        PRIVATE_VAULT_PATH="vaults/live",
    )
    assert vault is None
    assert "same path as PRIVATE_VAULT_PATH" in reason


def test_relative_forms_of_one_directory_are_refused(rel_project, elsewhere, monkeypatch):
    vault, reason = _resolve_in(
        rel_project, elsewhere, monkeypatch,
        VAULT_PATH_TEST="vaults/test", PRIVATE_VAULT_PATH="./vaults/live/../test",
    )
    assert vault is None
    assert "same path as PRIVATE_VAULT_PATH" in reason


def test_relative_test_vault_only_under_cwd_is_refused(rel_project, elsewhere, monkeypatch):
    """Exists under the cwd only: the project-root resolution refuses it."""
    (elsewhere / "only_here").mkdir()
    vault, reason = _resolve_in(rel_project, elsewhere, monkeypatch, VAULT_PATH_TEST="only_here")
    assert vault is None
    assert "existing directory" in reason


def test_post_commit_and_vault_guard_agree_on_the_live_vault(rel_project, elsewhere, monkeypatch):
    """One relative PRIVATE_VAULT_PATH names one directory in both hooks."""
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(rel_project))
    monkeypatch.setenv("PRIVATE_VAULT_PATH", "vaults/live")
    monkeypatch.chdir(elsewhere)
    guarded = [Path(v.path).resolve() for v in load_hook("vault_guard.py").vault_dirs()]
    from_hook = Path(load_hook(POST_COMMIT).project_path("vaults/live")).resolve()
    assert guarded == [from_hook] == [(rel_project / "vaults" / "live").resolve()]


def _synthetic_commit_project(tmp_path: Path) -> Path:
    """A tmp git project whose last commit touches src/context/, with a stub
    tools/eval_retrieval.py."""
    project = tmp_path / "proj"
    (project / "src" / "context").mkdir(parents=True)
    (project / "tools").mkdir()
    (project / "tools" / "eval_retrieval.py").write_text("print('EVAL STUB ran')\n", encoding="utf-8")
    git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false"]
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], cwd=project, check=True)
    (project / "src" / "context" / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run([*git, "add", "-A"], cwd=project, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "touch context"], cwd=project, check=True)
    return project


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git for a synthetic repo")
@pytest.mark.parametrize("same", [False, True], ids=["distinct-runs", "same-refused"])
def test_post_commit_relative_values_from_another_cwd(tmp_path, elsewhere, same):
    """End to end through the real hook process from an unrelated cwd.
    Distinct relative vaults run the eval; a relative live vault naming the
    test vault is refused (positive control: the stub must not run)."""
    project = _synthetic_commit_project(tmp_path)
    _make_vault_dirs(project, elsewhere)
    env = {
        "CLAUDE_PROJECT_DIR": str(project),
        "VAULT_PATH_TEST": str(project / "vaults" / "live") if same else "vaults/test",
        "PRIVATE_VAULT_PATH": "vaults/live",
    }
    payload = {"tool_name": "Bash", "tool_input": {"command": 'git commit -m "x"'}}
    result = run_hook(POST_COMMIT, payload, cwd=elsewhere, env=env)
    assert result.returncode == 0
    if same:
        assert "EVAL STUB ran" not in result.stdout
        assert "same path as PRIVATE_VAULT_PATH" in result.stdout
    else:
        assert "EVAL STUB ran" in result.stdout, result.stdout
        assert f"Vault: {(project / 'vaults' / 'test').resolve()}" in result.stdout
