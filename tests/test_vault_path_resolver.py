"""
tests/test_vault_path_resolver.py

One resolution rule for a configured vault path (src/core/vault_path.py),
shared by the app (src/core/config.get_private_vault_path), the hooks
(.claude/hooks/hook_utils.project_path, used by vault_guard.py and
post_commit_eval.py) and the retrieval-ablation runner.

Before the shared resolver, the app resolved a relative PRIVATE_VAULT_PATH
against the process cwd while the hooks and the runner resolved it against
the project root, so one value could name two directories.

Everything here runs under tmp_path. The project root of each caller is
pointed at a tmp project (vault_path.PROJECT_ROOT, CLAUDE_PROJECT_DIR,
runner.REPO_ROOT); no test reads an env file or the real vault.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import src.core.config as config
import src.core.vault_path as vault_path
from src.core.vault_path import resolve_configured_path
from tests.conftest import hook_output, hook_payload, load_hook, make_vault_dirs, run_hook
from tools.retrieval_ablation import runner

RELATIVE = "vaults/live"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """A tmp project as every caller's root, with no vault override active.
    The cwd is left to each test."""
    root = tmp_path / "proj"
    root.mkdir()
    monkeypatch.setattr(vault_path, "PROJECT_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(root))
    monkeypatch.setattr(runner, "REPO_ROOT", root)
    config.clear_vault_path_override()
    return root


def _cwd(kind: str, project: Path, tmp_path: Path) -> Path:
    """The three cwds: the project root, a subdirectory of it, and an
    unrelated tmp directory. The last two get the same vault layout as the
    project (make_vault_dirs), so a cwd-relative resolution would find a
    real but wrong directory instead of failing on a missing one."""
    if kind == "root":
        return project
    cwd = project / "sub" if kind == "subdir" else tmp_path / "elsewhere"
    cwd.mkdir()
    make_vault_dirs(cwd)
    return cwd


CWDS = ("root", "subdir", "unrelated")


def _all_callers(value: str) -> dict[str, Path]:
    """`value` as PRIVATE_VAULT_PATH, resolved by each caller in the
    current process state."""
    guard = load_hook("vault_guard.py")
    post_commit = load_hook("post_commit_eval.py")
    configured = [
        Path(v.path) for v in guard.vault_dirs()
        if os.path.basename(v.path) != "private_vault"
    ]
    assert len(configured) == 1, configured
    return {
        "app": config.get_private_vault_path(),
        "vault_guard": configured[0],
        "post_commit_eval": post_commit._configured_path(value),
        "runner": runner._configured_path(value),
    }


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kind", CWDS)
def test_relative_value_names_one_directory_from_every_caller(kind, project, tmp_path, monkeypatch):
    make_vault_dirs(project)
    cwd = _cwd(kind, project, tmp_path)
    monkeypatch.setenv("PRIVATE_VAULT_PATH", RELATIVE)
    monkeypatch.chdir(cwd)

    expected = (project / "vaults" / "live").resolve()
    resolved = _all_callers(RELATIVE)
    assert resolved == dict.fromkeys(resolved, expected)


@pytest.mark.parametrize("kind", ("subdir", "unrelated"))
def test_cwd_relative_resolution_would_name_a_different_directory(kind, project, tmp_path, monkeypatch):
    """Control for the test above: away from the project root, the old
    cwd-relative rule names a different, existing directory, so the
    agreement test can tell the two rules apart."""
    make_vault_dirs(project)
    cwd = _cwd(kind, project, tmp_path)
    monkeypatch.chdir(cwd)

    monkeypatch.setenv("PRIVATE_VAULT_PATH", RELATIVE)

    cwd_rule = Path(RELATIVE).resolve()
    assert cwd_rule.is_dir()
    assert cwd_rule != (project / "vaults" / "live").resolve()
    assert config.get_private_vault_path() != cwd_rule


@pytest.mark.parametrize("kind", CWDS)
def test_absolute_value_passes_through(kind, project, tmp_path, monkeypatch):
    absolute = tmp_path / "abs_vault"
    absolute.mkdir()
    cwd = _cwd(kind, project, tmp_path)
    monkeypatch.setenv("PRIVATE_VAULT_PATH", str(absolute))
    monkeypatch.chdir(cwd)

    resolved = _all_callers(str(absolute))
    assert resolved == dict.fromkeys(resolved, absolute.resolve())


def test_hook_process_from_unrelated_cwd_guards_the_project_vault(project, tmp_path):
    """End to end through a bare hook process: the shared resolver imports
    from the hook's own checkout whatever the cwd, and a relative value
    names the project's directory. Allow side is the positive control: the
    same layout under the cwd is not guarded."""
    make_vault_dirs(project)
    elsewhere = _cwd("unrelated", project, tmp_path)
    env = {"CLAUDE_PROJECT_DIR": str(project), "PRIVATE_VAULT_PATH": RELATIVE}

    def read(target: Path):
        payload = hook_payload("Read", cwd=str(elsewhere), file_path=str(target / "x.json"))
        result = run_hook("vault_guard.py", payload, cwd=elsewhere, env=env)
        assert result.returncode == 0, result.stderr
        return hook_output(result)

    denied = read(project / "vaults" / "live")
    assert denied is not None and denied["permissionDecision"] == "deny"
    assert read(elsewhere / "vaults" / "live") is None


def test_real_anchors_are_one_checkout(monkeypatch):
    """Unpatched, the app's, the hooks' and the runner's project roots are
    the same directory, so the agreement above holds outside tests. Path
    arithmetic only; nothing is read."""
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    hook_utils = load_hook("hook_utils.py")

    def canon(p) -> str:
        return os.path.normcase(os.path.realpath(p))

    roots = {
        "vault_path": canon(vault_path.PROJECT_ROOT),
        "hooks": canon(hook_utils.project_dir()),
        "runner": canon(runner.REPO_ROOT),
        "config_env_file_dir": canon(config.ENV_FILE.parent),
    }
    assert len(set(roots.values())) == 1, roots


# ---------------------------------------------------------------------------
# Unset, empty, whitespace
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", (None, "", "   "), ids=("unset", "empty", "whitespace"))
def test_unset_value_raises_as_before(value, project, monkeypatch):
    if value is None:
        monkeypatch.delenv("PRIVATE_VAULT_PATH", raising=False)
    else:
        monkeypatch.setenv("PRIVATE_VAULT_PATH", value)
    with pytest.raises(ValueError, match="PRIVATE_VAULT_PATH not set in environment"):
        config.get_private_vault_path()
    assert resolve_configured_path(value) is None


def test_set_value_does_not_raise(project, monkeypatch):
    """Positive control for the test above."""
    monkeypatch.setenv("PRIVATE_VAULT_PATH", RELATIVE)
    assert config.get_private_vault_path() == (project / "vaults" / "live").resolve()


@pytest.mark.parametrize("value", ("", "   "), ids=("empty", "whitespace"))
def test_unset_value_leaves_hooks_and_runner_unconfigured(value, project, monkeypatch):
    monkeypatch.setenv("PRIVATE_VAULT_PATH", value)
    monkeypatch.setenv("VAULT_PATH_TEST", value)
    # The tmp project has no env file, so nothing else can configure it.
    assert not (project / ".env").exists()
    assert load_hook("vault_guard.py").vault_dirs() == []
    assert runner.resolve_test_vault() == (None, "VAULT_PATH_TEST is not set")


# ---------------------------------------------------------------------------
# The resolver itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "value",
    ("vaults/a/../live", "vaults/live/", "vaults\\live", "./vaults/live", "no/such/leaf"),
)
def test_matches_path_resolve(value, project):
    """realpath is what Path.resolve() returns for the same joined path."""
    assert resolve_configured_path(value) == str((project / value).resolve())


def test_expands_home(project, monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    assert resolve_configured_path("~/vault") == str((home / "vault").resolve())


def test_explicit_root_overrides_default(project, tmp_path):
    other = tmp_path / "other_root"
    assert resolve_configured_path(RELATIVE, str(other)) == str((other / RELATIVE).resolve())
    assert resolve_configured_path(RELATIVE) == str((project / RELATIVE).resolve())


def test_vault_swap_override_resolves_by_the_same_rule(project, tmp_path, monkeypatch):
    """The swap override and a deferred-work binding go through the rule
    too, so a relative VAULT_PATH_* value swapped in names the project's
    directory from any cwd."""
    make_vault_dirs(project)
    monkeypatch.chdir(_cwd("unrelated", project, tmp_path))
    expected = (project / "vaults" / "test").resolve()

    config.set_vault_path_override("vaults/test", "test")
    assert config.get_private_vault_path() == expected
    config.clear_vault_path_override()

    with config.vault_binding("vaults/test"):
        assert config.get_private_vault_path() == expected
