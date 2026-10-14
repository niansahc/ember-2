"""
tests/test_env_file_loading.py

.env is loaded by process entrypoints, never by importing src.

config.py used to call load_dotenv() at import, so every process that
imported config -- the pytest process among them -- took PRIVATE_VAULT_PATH
and the rest of .env into os.environ. Loading now happens only in
load_env_file(), called by src/api/asgi.py (the uvicorn target) and by
scripts and tools under their __main__ guard.

Every probe runs in a fresh interpreter, because this process's conftest has
already pinned the environment, and reads a synthetic .env that holds a
sentinel key, never the repo's .env. The four probes run at once (one
module-scoped fixture); each is still its own interpreter.
"""

import json
import shutil
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT, run_fresh_pythons

_SENTINEL = "EMBER_TEST_ENV_FILE_SENTINEL"


def _probe_json(out) -> dict:
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


# -- config import, in a copy of config.py beside a synthetic .env ------------
#
# A minimal tree, <root>/src/core/config.py with <root>/.env. Both the old
# import-time load_dotenv() (searches upward from config.py) and ENV_FILE
# (config.py's parents[2]) find this .env, not the repo's. The probe runs from
# a script file, not `python -c`: without a __main__.__file__, find_dotenv()
# treats the process as interactive and searches the working directory (the
# repo) instead, and an old-style import-time load would go unseen here.

_CONFIG_PROBE = """
import json, os, sys
sys.path.insert(0, sys.argv[1])
import src.core.config as c
assert c.__file__.startswith(sys.argv[1]), c.__file__
if sys.argv[2] == "load":
    c.load_env_file()
print(json.dumps({"sentinel": os.environ.get(%r)}))
""" % _SENTINEL


def _config_job(tmp_path: Path, mode: str):
    root = _config_tree(tmp_path)
    probe = tmp_path / "config_probe.py"
    probe.write_text(_CONFIG_PROBE, encoding="utf-8")
    return [str(probe), str(root), mode], tmp_path


def _config_tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src" / "core").mkdir(parents=True)
    (root / "src" / "__init__.py").touch()
    (root / "src" / "core" / "__init__.py").touch()
    shutil.copy(REPO_ROOT / "src" / "core" / "config.py", root / "src" / "core")
    (root / ".env").write_text(f"{_SENTINEL}=from-env-file\n", encoding="utf-8")
    return root


# -- app module vs API entrypoint, in the repo, with ENV_FILE redirected -------
#
# Same synthetic .env for both; only the imported module differs. The
# entrypoint probe also drops PRIVATE_VAULT_PATH from its environment, so the
# vault it resolves can only have come from the .env file. The app-module
# probe keeps it (run_fresh_python points it at a stand-in), because the app
# cannot import without a vault.

_APP_PROBE = """
import json, os, sys
from pathlib import Path
import src.core.config as c
c.ENV_FILE = Path(sys.argv[1])
module = sys.argv[2]
if module == "src.api.asgi":
    os.environ.pop("PRIVATE_VAULT_PATH", None)
__import__(module)
print(json.dumps({
    "sentinel": os.environ.get(%r),
    "vault": str(c.get_private_vault_path()),
}))
""" % _SENTINEL


def _app_job(tmp_path: Path, module: str):
    """Returns (job, vault named in the .env file). The job's vault is the
    stand-in."""
    env_vault = tmp_path / "vault_named_in_env_file"
    env_vault.mkdir()
    env_file = tmp_path / "synthetic.env"
    env_file.write_text(
        f"{_SENTINEL}=from-env-file\nPRIVATE_VAULT_PATH={env_vault}\n",
        encoding="utf-8",
    )
    stand_in = tmp_path / "stand_in_vault"
    stand_in.mkdir()
    return (["-c", _APP_PROBE, str(env_file), module], stand_in), env_vault


@pytest.fixture(scope="module")
def probes(tmp_path_factory):
    """All four probes, run concurrently. Values: (CompletedProcess, the
    vault the test expects the probe to resolve)."""
    module_job, _ = _app_job(tmp_path_factory.mktemp("app_module"), "src.api.main")
    asgi_job, asgi_env_vault = _app_job(tmp_path_factory.mktemp("app_entrypoint"), "src.api.asgi")
    jobs = {
        "config_import": _config_job(tmp_path_factory.mktemp("config_import"), "import"),
        "config_load": _config_job(tmp_path_factory.mktemp("config_load"), "load"),
        "app_module": module_job,
        "app_entrypoint": asgi_job,
    }
    results = run_fresh_pythons(jobs)
    # Each probe should resolve its own vault, except the entrypoint, which
    # loads the .env file and resolves the vault named there.
    expected_vault = {key: job[1] for key, job in jobs.items()}
    expected_vault["app_entrypoint"] = asgi_env_vault
    return {key: (results[key], expected_vault[key]) for key in jobs}


def test_config_import_does_not_load_env_file(probes):
    out, _ = probes["config_import"]
    assert _probe_json(out)["sentinel"] is None


def test_control_load_env_file_loads_the_same_env_file(probes):
    out, _ = probes["config_load"]
    assert _probe_json(out)["sentinel"] == "from-env-file"


def test_app_module_import_does_not_load_env_file(probes):
    out, stand_in = probes["app_module"]

    probe = _probe_json(out)
    assert probe["sentinel"] is None
    assert probe["vault"] == str(stand_in.resolve())


def test_control_api_entrypoint_loads_env_file_and_resolves_vault_from_it(probes):
    out, env_vault = probes["app_entrypoint"]

    probe = _probe_json(out)
    assert probe["sentinel"] == "from-env-file"
    assert probe["vault"] == str(env_vault.resolve())
