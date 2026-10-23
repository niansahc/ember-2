"""
tests/test_ollama_guard.py

The Ollama stub and guard in tests/ollama_guard.py. The guard is itself an
absence assertion (no test connected to port 11434), so each part has a
positive control here: the guard records and fails a connection that does
happen, the stub refuses a request the client does make, and the stub leaves
other ports alone.

No test here opens a real connection to 11434: every attempt is refused
before the socket connects.
"""

from __future__ import annotations

import http.server
import socket
import threading

import httpx
import ollama
import pytest

from tests import ollama_guard
from tests.ollama_guard import OllamaConnectionBlocked


def test_raw_socket_to_the_ollama_port_is_refused_and_recorded():
    with pytest.raises(OllamaConnectionBlocked):
        socket.create_connection(("127.0.0.1", 11434), timeout=1)
    # Taken here so this test passes; the hook would otherwise fail it.
    assert ollama_guard.take_violations() == ["127.0.0.1:11434"]


def test_connect_on_a_literal_address_is_refused_and_recorded():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OllamaConnectionBlocked):
            sock.connect(("127.0.0.1", 11434))
    finally:
        sock.close()
    assert ollama_guard.take_violations() == ["127.0.0.1:11434"]


def test_httpx_to_the_ollama_port_is_stubbed_without_a_socket():
    with pytest.raises(httpx.ConnectError, match="stubbed"):
        httpx.get("http://127.0.0.1:11434/api/version", timeout=1.0)
    # The stub answers before a socket is opened, so the guard saw nothing.
    # Control: the two socket tests above, where an attempt is recorded.
    assert ollama_guard.take_violations() == []


def test_ollama_client_sees_the_model_as_unreachable():
    with pytest.raises(ConnectionError):
        ollama.Client(host="http://127.0.0.1:11434").list()
    assert ollama_guard.take_violations() == []


def test_the_stub_leaves_other_ports_alone():
    """Control for the stub: a local server on an ephemeral port answers."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    # One request, so handle_request: serve_forever's shutdown waits a poll tick.
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        # trust_env=False: an HTTP_PROXY in the shell would carry loopback
        # traffic to the proxy instead of this server.
        resp = httpx.get(f"http://127.0.0.1:{port}/", timeout=5.0, trust_env=False)
        assert resp.status_code == 200 and resp.text == "ok"
    finally:
        thread.join(timeout=5.0)
        server.server_close()


_TWO_TESTS = """
import socket

def test_connects_and_swallows_the_error():
    try:
        socket.create_connection(("127.0.0.1", 11434), timeout=1)
    except OSError:
        pass

def test_does_not_connect():
    assert True
"""


def test_a_swallowed_connection_attempt_still_fails_its_test(pytester):
    """End to end: code that catches the refusal (as Stage 3 catches every
    exception) cannot hide the attempt. The second test is the control."""
    pytester.makepyfile(test_two=_TWO_TESTS)
    result = pytester.runpytest("-p", "tests.ollama_guard", "-p", "no:cacheprovider")
    result.assert_outcomes(failed=1, passed=1)
    result.stdout.fnmatch_lines(["*opened a connection to Ollama port 11434*"])


_CONNECT_THEN_OTHER_OUTCOME = """
import socket

import pytest

def _connect():
    try:
        socket.create_connection(("127.0.0.1", 11434), timeout=1)
    except OSError:
        pass

def test_connects_then_skips():
    _connect()
    pytest.skip("skipped after connecting")

def test_connects_then_xfails():
    _connect()
    pytest.xfail("xfailed after connecting")

@pytest.mark.xfail(reason="marker")
def test_connects_and_fails_under_an_xfail_marker():
    _connect()
    assert False

@pytest.mark.xfail(reason="marker")
def test_connects_and_passes_under_an_xfail_marker():
    _connect()

def test_skips_without_connecting():
    pytest.skip("control")
"""


def test_a_connection_is_reported_whatever_the_test_outcome(pytester):
    """A skip, an xfail or an xfail marker cannot absorb the attempt: it is
    reported as a teardown error. The test that skips without connecting is
    the control (skipped, no error)."""
    pytester.makepyfile(test_outcomes=_CONNECT_THEN_OTHER_OUTCOME)
    result = pytester.runpytest("-p", "tests.ollama_guard", "-p", "no:cacheprovider")
    result.assert_outcomes(skipped=2, xfailed=2, xpassed=1, errors=4)
    result.stdout.fnmatch_lines(["*opened a connection to Ollama port 11434 during the test*"])


_LIVE_TEST = """
import pytest
from tests import ollama_guard

@pytest.mark.live_ollama
def test_live():
    assert ollama_guard.is_allowed()

@pytest.mark.eval
def test_eval():
    assert ollama_guard.is_allowed()

def test_offline():
    assert not ollama_guard.is_allowed()
"""


def test_live_ollama_and_eval_tests_run_with_the_guard_stood_down(pytester):
    """Selected, the live and eval tests run with the guard allowing them,
    while the offline test in the same session still has the guard on."""
    pytester.makepyfile(test_live=_LIVE_TEST)
    result = pytester.runpytest("-p", "tests.ollama_guard", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=3)


def test_the_suite_deselects_live_ollama_and_eval_by_default(pytestconfig):
    """The suite's own addopts carry the deselection (tests/pytest.ini), so
    the guard is on for every test a default run selects."""
    addopts = " ".join(pytestconfig.getini("addopts"))
    assert "not eval" in addopts
    assert "not live_ollama" in addopts
