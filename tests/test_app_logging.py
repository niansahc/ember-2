"""
tests/test_app_logging.py

configure_app_logging() in src/api/main.py: one basicConfig plus a
RotatingFileHandler, path from get_ember_log_path() (EMBER_LOG_PATH), default outside the repo tree.

Behavior at import time is checked in a fresh interpreter: under pytest the
root logger already carries capture handlers, which makes basicConfig a
no-op in-process.
"""

import json
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT, run_fresh_pythons

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


def _probe_job(tmp_path, log_path):
    return ["-c", _PROBE], tmp_path / "vault", {"EMBER_LOG_PATH": str(log_path)}


def _probe_json(out):
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def probes(tmp_path_factory):
    """Both fresh-interpreter imports, run concurrently: a writable log path
    (shared by the writable-path tests) and one whose parent is a file."""
    writable_dir = tmp_path_factory.mktemp("app_logging")
    writable_log = writable_dir / "logs" / "ember-api.log"
    unwritable_dir = tmp_path_factory.mktemp("app_logging_unwritable")
    blocker = unwritable_dir / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    results = run_fresh_pythons({
        "writable": _probe_job(writable_dir, writable_log),
        "unwritable": _probe_job(unwritable_dir, blocker / "ember-api.log"),
    })
    return {
        "writable": (writable_log, _probe_json(results["writable"])),
        "unwritable": _probe_json(results["unwritable"]),
    }


@pytest.fixture(scope="module")
def probe(probes):
    return probes["writable"]


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


def test_unwritable_path_does_not_stop_import(probes):
    result = probes["unwritable"]
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
