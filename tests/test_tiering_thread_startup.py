"""
tests/test_tiering_thread_startup.py

The nightly tiering thread starts at app startup (the FastAPI lifespan that
uvicorn runs), not as a side effect of importing src.api.main, so processes
that only import the app -- every pytest process during collection, scripts,
probes -- never run it.

Checked in a fresh interpreter so the count reflects only what import and
startup did, not threads left by earlier tests in this process.
"""

import json

import pytest

from tests.conftest import run_fresh_python

_PROBE = r"""
import json, threading

def tiering_threads():
    return sum(t.name == "ember-nightly-tiering" for t in threading.enumerate())

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
    out = run_fresh_python(["-c", _PROBE], tmp_path_factory.mktemp("tiering_probe_vault"))
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_import_does_not_start_the_tiering_thread(counts):
    assert counts["after_import"] == 0


def test_control_app_startup_starts_the_tiering_thread(counts):
    # Control for the zero above: the probe does see the thread once it runs.
    assert counts["after_startup"] == 1


def test_repeated_startup_does_not_stack_tiering_threads(counts):
    assert counts["after_second_startup"] == 1
