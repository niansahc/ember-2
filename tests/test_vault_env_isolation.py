"""
tests/test_vault_env_isolation.py

The environment fallback for the vault path must never name a real vault
inside a pytest process.

tests/conftest.py sets the runtime override in a session fixture, which runs
only after collection has imported every test module. Before that, and in
every window where the override is cleared, get_private_vault_path() falls
back to PRIVATE_VAULT_PATH. config.py fills that from .env at import, so
collection alone used to write <real vault>/system/nature_version.txt (the
app import builds LLMAdapter singletons, which load the nature document).

The end-to-end check runs pytest collection in a fresh interpreter with
PRIVATE_VAULT_PATH pointing at a stand-in "live" vault, the way .env would,
and asserts nothing lands there. Its control imports the app in a fresh
interpreter without the conftest and shows the same stand-in does receive
the write, so an empty stand-in means isolation, not a probe that cannot see.
"""

import os
import subprocess
import sys
from pathlib import Path

from src.core.config import clear_vault_path_override, get_private_vault_path

REPO_ROOT = Path(__file__).resolve().parents[1]

# A test module that imports the app at module level, so collecting it runs
# the import-time LLMAdapter construction.
_COLLECT_TARGET = REPO_ROOT / "tests" / "test_health_check.py"


def _run_fresh(args: list[str], stand_in_live_vault: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PRIVATE_VAULT_PATH"] = str(stand_in_live_vault)
    env.pop("PYTEST_ADDOPTS", None)
    return subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )


def _files_under(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def _resolves_to_isolation_vault(env_fallback_vault: Path) -> bool:
    """True when the override-cleared resolution lands in the throwaway vault."""
    clear_vault_path_override()
    return get_private_vault_path() == env_fallback_vault.resolve()


# -- end to end: collection ----------------------------------------------------


def test_collect_target_imports_the_app_at_module_level():
    # Guard for the next test: if the target stops importing the app, its
    # collection would no longer exercise the import-time write at all.
    assert "from src.api.main import app" in _COLLECT_TARGET.read_text(encoding="utf-8")


def test_collection_writes_nothing_to_the_env_vault(tmp_path):
    stand_in = tmp_path / "stand_in_live_vault"
    stand_in.mkdir()

    out = _run_fresh(
        ["-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
         str(_COLLECT_TARGET.relative_to(REPO_ROOT))],
        stand_in,
    )

    assert out.returncode == 0, (out.stdout + out.stderr)[-2000:]
    assert _files_under(stand_in) == []


def test_control_app_import_without_conftest_writes_to_the_env_vault(tmp_path):
    stand_in = tmp_path / "stand_in_live_vault"
    stand_in.mkdir()

    out = _run_fresh(["-c", "import src.api.main"], stand_in)

    assert out.returncode == 0, out.stderr[-2000:]
    assert "system/nature_version.txt" in _files_under(stand_in)


# -- in process: the override-cleared fallback --------------------------------


def test_cleared_override_resolves_to_the_isolation_vault(env_fallback_vault):
    assert _resolves_to_isolation_vault(env_fallback_vault)


def test_control_cleared_override_with_a_live_env_path_is_flagged(
    env_fallback_vault, tmp_path, monkeypatch
):
    monkeypatch.setenv("PRIVATE_VAULT_PATH", str(tmp_path / "stand_in_live_vault"))
    assert not _resolves_to_isolation_vault(env_fallback_vault)


def test_dotenv_loading_is_disabled_in_this_process(env_fallback_vault):
    import dotenv

    # Neutered at conftest import: a reload of src.core.config cannot pull
    # .env values (PRIVATE_VAULT_PATH among them) back into os.environ.
    assert dotenv.load_dotenv() is False
    assert os.environ["PRIVATE_VAULT_PATH"] == str(env_fallback_vault)
