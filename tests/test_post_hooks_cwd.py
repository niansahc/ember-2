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

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = REPO_ROOT / ".claude" / "hooks"
POST_EDIT = HOOKS_DIR / "post_edit_test.py"
POST_COMMIT = HOOKS_DIR / "post_commit_eval.py"


def _run_hook(hook: Path, payload: dict, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
        cwd=cwd,
        env={**os.environ, **env},
    )


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
    result = _run_hook(POST_EDIT, payload, elsewhere, {"CLAUDE_PROJECT_DIR": str(project)})
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
    subprocess.run([*git, "add", "-A"], cwd=project, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], cwd=project, check=True)
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
    result = _run_hook(POST_COMMIT, payload, elsewhere, env)
    assert result.returncode == 0
    assert f"EVAL STUB cwd={project}" in result.stdout, result.stdout


@pytest.mark.parametrize("hook", [POST_EDIT, POST_COMMIT], ids=lambda p: p.name)
def test_repo_root_falls_back_to_hook_location(hook, monkeypatch):
    """Without CLAUDE_PROJECT_DIR (a manual run), the anchor is the
    checkout the hook file lives in, not the cwd."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    spec = importlib.util.spec_from_file_location(f"{hook.stem}_under_test", hook)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.REPO_ROOT == REPO_ROOT
