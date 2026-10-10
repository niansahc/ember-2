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

The end-to-end check reproduces collection in a fresh interpreter -- run the
conftest, then import the app -- with PRIVATE_VAULT_PATH pointing at a
stand-in "live" vault, the way .env would, and asserts nothing lands there.
Its control imports the app without the conftest and shows the same stand-in
does receive the write, so an empty stand-in means isolation, not a probe
that cannot see.
"""

from pathlib import Path

from src.core.config import clear_vault_path_override, get_private_vault_path
from tests.conftest import run_fresh_python

_CONFTEST_THEN_APP = (
    "import runpy; runpy.run_path('tests/conftest.py'); import src.api.main"
)


def _files_under(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def _resolves_to_isolation_vault(env_fallback_vault: Path) -> bool:
    """True when the override-cleared resolution lands in the throwaway vault."""
    clear_vault_path_override()
    return get_private_vault_path() == env_fallback_vault.resolve()


# -- end to end: conftest before app import ------------------------------------


def test_app_import_after_conftest_writes_nothing_to_the_env_vault(tmp_path):
    stand_in = tmp_path / "stand_in_live_vault"
    stand_in.mkdir()

    out = run_fresh_python(["-c", _CONFTEST_THEN_APP], stand_in)

    assert out.returncode == 0, out.stderr[-2000:]
    assert _files_under(stand_in) == []


def test_control_app_import_without_conftest_writes_to_the_env_vault(tmp_path):
    stand_in = tmp_path / "stand_in_live_vault"
    stand_in.mkdir()

    out = run_fresh_python(["-c", "import src.api.main"], stand_in)

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


def test_dotenv_loading_is_disabled_in_this_process():
    import dotenv

    # Neutered at conftest import: a reload of src.core.config cannot pull
    # .env values (PRIVATE_VAULT_PATH among them) back into os.environ.
    assert dotenv.load_dotenv() is False
