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
sentinel key, never the repo's .env.
"""

import json
import shutil
from pathlib import Path

from tests.conftest import REPO_ROOT, run_fresh_python

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


def _run_config_probe(tmp_path: Path, mode: str):
    root = _config_tree(tmp_path)
    probe = tmp_path / "config_probe.py"
    probe.write_text(_CONFIG_PROBE, encoding="utf-8")
    return run_fresh_python([str(probe), str(root), mode], tmp_path)


def _config_tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src" / "core").mkdir(parents=True)
    (root / "src" / "__init__.py").touch()
    (root / "src" / "core" / "__init__.py").touch()
    shutil.copy(REPO_ROOT / "src" / "core" / "config.py", root / "src" / "core")
    (root / ".env").write_text(f"{_SENTINEL}=from-env-file\n", encoding="utf-8")
    return root


def test_config_import_does_not_load_env_file(tmp_path):
    out = _run_config_probe(tmp_path, "import")
    assert _probe_json(out)["sentinel"] is None


def test_control_load_env_file_loads_the_same_env_file(tmp_path):
    out = _run_config_probe(tmp_path, "load")
    assert _probe_json(out)["sentinel"] == "from-env-file"


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


def _synthetic_env_file(tmp_path: Path) -> tuple[Path, Path]:
    env_vault = tmp_path / "vault_named_in_env_file"
    env_vault.mkdir()
    env_file = tmp_path / "synthetic.env"
    env_file.write_text(
        f"{_SENTINEL}=from-env-file\nPRIVATE_VAULT_PATH={env_vault}\n",
        encoding="utf-8",
    )
    return env_file, env_vault


def test_app_module_import_does_not_load_env_file(tmp_path):
    env_file, _ = _synthetic_env_file(tmp_path)
    stand_in = tmp_path / "stand_in_vault"
    stand_in.mkdir()

    out = run_fresh_python(["-c", _APP_PROBE, str(env_file), "src.api.main"], stand_in)

    probe = _probe_json(out)
    assert probe["sentinel"] is None
    assert probe["vault"] == str(stand_in.resolve())


def test_control_api_entrypoint_loads_env_file_and_resolves_vault_from_it(tmp_path):
    env_file, env_vault = _synthetic_env_file(tmp_path)
    stand_in = tmp_path / "stand_in_vault"
    stand_in.mkdir()

    out = run_fresh_python(["-c", _APP_PROBE, str(env_file), "src.api.asgi"], stand_in)

    probe = _probe_json(out)
    assert probe["sentinel"] == "from-env-file"
    assert probe["vault"] == str(env_vault.resolve())
