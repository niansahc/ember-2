"""
tests/test_app_logging.py

configure_app_logging() in src/api/main.py: one basicConfig plus a
RotatingFileHandler, path from get_ember_log_path() (EMBER_LOG_PATH), default outside the repo tree.

Behavior at import time is checked in a fresh interpreter: under pytest the
root logger already carries capture handlers, which makes basicConfig a
no-op in-process.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_PROBE = r"""
import json, logging, os
before_httpx = logging.getLogger("httpx").level
import src.api.main as m
root = logging.getLogger()
handlers = [h for h in root.handlers if h.__class__.__name__ == "RotatingFileHandler"]
first = len(handlers)
returned_again = m.configure_app_logging()
handlers2 = [h for h in root.handlers if h.__class__.__name__ == "RotatingFileHandler"]
logging.getLogger("ember.probe").info("probe-line-info")
logging.getLogger("httpx").info("httpx-probe-line")
try:
    raise RuntimeError("PROBE-EXC-MARKER")
except RuntimeError as exc:
    logging.getLogger("ember.probe").error("probe-exc-args: %s", exc)
    logging.getLogger("ember.probe").error("probe-exc-info", exc_info=exc)
for h in root.handlers:
    h.flush()
print(json.dumps({
    "first": first,
    "second": len(handlers2),
    "paths": [h.baseFilename for h in handlers2],
    "returned_again": str(returned_again) if returned_again else None,
    "before_httpx": before_httpx,
    "after_httpx": logging.getLogger("httpx").level,
    "filters": [[f.__class__.__name__ for f in h.filters] for h in handlers2],
}))
"""


def _run_probe(tmp_path, log_path):
    import os

    env = dict(os.environ)
    env["EMBER_LOG_PATH"] = str(log_path)
    env["PRIVATE_VAULT_PATH"] = str(tmp_path / "vault")
    out = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    """One fresh-interpreter import shared by the writable-path tests."""
    tmp_path = tmp_path_factory.mktemp("app_logging")
    log_path = tmp_path / "logs" / "ember-api.log"
    return log_path, _run_probe(tmp_path, log_path)


def test_import_attaches_one_rotating_handler_at_env_path(probe):
    log_path, result = probe
    assert result["first"] == 1
    assert [Path(p) for p in result["paths"]] == [log_path]
    assert log_path.exists()
    assert "probe-line-info" in log_path.read_text(encoding="utf-8")


def test_reconfigure_does_not_stack_handlers(probe):
    _, result = probe
    assert result["first"] == 1       # control: a handler was attached
    assert result["second"] == 1      # still one after a second call


def test_httpx_is_held_at_warning(probe):
    log_path, result = probe
    assert result["before_httpx"] == 0   # control: NOTSET without the config
    assert result["after_httpx"] == 30   # logging.WARNING
    assert "httpx-probe-line" not in log_path.read_text(encoding="utf-8")


def test_file_handler_redacts_exception_text(probe):
    log_path, result = probe
    assert result["filters"] == [["ExceptionTextRedactor"]]
    text = log_path.read_text(encoding="utf-8")
    assert "PROBE-EXC-MARKER" not in text
    # Control: both lines did reach the file, with the type name.
    assert "probe-exc-args: RuntimeError" in text
    assert "probe-exc-info" in text and "Traceback (most recent call last):" in text
    # Marker-present control without the filter: tests/test_log_redaction.py.


def test_unwritable_path_does_not_stop_import(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    result = _run_probe(tmp_path, blocker / "ember-api.log")
    assert result["first"] == 0
    assert result["returned_again"] is None


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO_ROOT)
        return True
    except ValueError:
        return False


def test_default_path_is_outside_the_repo_tree():
    from src.core.config import default_app_log_path

    default = default_app_log_path()
    assert not _inside_repo(default)
    assert default.name == "ember-api.log"
    # Control: the predicate does flag a path under the repo.
    assert _inside_repo(REPO_ROOT / "logs" / "ember-api.log")
