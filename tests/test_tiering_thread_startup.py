"""
tests/test_tiering_thread_startup.py

The nightly tiering thread starts at app startup (the FastAPI lifespan that
uvicorn runs), not as a side effect of importing src.api.main.

Started at import, the thread came up in every process that imported the app
-- including every pytest process during collection -- unbound to any vault,
and would resolve whatever vault was active when 00:05 arrived.

Checked in a fresh interpreter so the count reflects only what import and
startup did, not threads left by earlier tests in this process.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_PROBE = r"""
import json, threading

def tiering_threads():
    return sum(
        1 for t in threading.enumerate()
        if getattr(getattr(t, "_target", None), "__name__", "") == "_nightly_tiering_loop"
    )

import src.api.main as m
from fastapi.testclient import TestClient

after_import = tiering_threads()
with TestClient(m.app):
    after_startup = tiering_threads()
with TestClient(m.app):
    after_second_startup = tiering_threads()

print(json.dumps({
    "after_import": after_import,
    "after_startup": after_startup,
    "after_second_startup": after_second_startup,
}))
"""


@pytest.fixture(scope="module")
def counts(tmp_path_factory):
    vault = tmp_path_factory.mktemp("tiering_probe_vault")
    env = dict(os.environ)
    env["PRIVATE_VAULT_PATH"] = str(vault)
    out = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_import_does_not_start_the_tiering_thread(counts):
    assert counts["after_import"] == 0


def test_control_app_startup_starts_the_tiering_thread(counts):
    # Control for the zero above: the probe does see the thread once it runs.
    assert counts["after_startup"] == 1


def test_repeated_startup_does_not_stack_tiering_threads(counts):
    assert counts["after_second_startup"] == 1
