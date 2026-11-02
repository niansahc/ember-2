"""
tests/test_aux_client.py

Auxiliary Ollama calls on the request path carry a real client timeout, log
their duration, and pass their kwargs through untouched.

These tests use the real ollama client against a local stub HTTP server, not
the conftest shim, so the httpx timeout itself is what is under test.
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from src.llm import aux_client

pytestmark = pytest.mark.real_aux_client


@pytest.fixture(autouse=True)
def fresh_clients():
    """Each test sets its own OLLAMA_HOST, read when a client is built."""
    aux_client._client_for_timeout.cache_clear()
    yield
    aux_client._client_for_timeout.cache_clear()


def _serve(handler_cls):
    # Threading, so shutdown() does not wait out a hung handler.
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class _Hangs(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        time.sleep(5)

    def log_message(self, *args):
        pass


class _Answers(BaseHTTPRequestHandler):
    seen_bodies: list = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        _Answers.seen_bodies.append(json.loads(self.rfile.read(length)))
        body = json.dumps({
            "model": "m",
            "message": {"role": "assistant", "content": "YES"},
            "done": True,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _Fails(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = b'{"error": "boom"}'
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _point_at(monkeypatch, server):
    monkeypatch.setenv("OLLAMA_HOST", f"127.0.0.1:{server.server_port}")


def test_hung_server_raises_timeout_and_logs_outcome(monkeypatch, caplog):
    server = _serve(_Hangs)
    _point_at(monkeypatch, server)
    caplog.set_level(logging.INFO, logger="ember.aux")

    start = time.perf_counter()
    with pytest.raises(httpx.TimeoutException):
        aux_client.aux_chat(
            tag="COACHING_FILTER", step="semantic check", timeout_s=0.5,
            model="m", messages=[{"role": "user", "content": "SECRET-TEXT"}],
        )
    elapsed = time.perf_counter() - start
    server.shutdown()

    assert elapsed < 3.0
    line = next(r.getMessage() for r in caplog.records
                if r.getMessage().startswith("[COACHING_FILTER] semantic check"))
    assert "outcome=timeout" in line
    assert "ms=" in line
    assert "SECRET-TEXT" not in caplog.text


def test_answering_server_returns_result_logs_ms_and_passes_kwargs(monkeypatch, caplog):
    """Positive control for the timeout test: the same path succeeds when the
    server answers, so the timeout test cannot pass by the call failing for
    some other reason."""
    _Answers.seen_bodies = []
    server = _serve(_Answers)
    _point_at(monkeypatch, server)
    caplog.set_level(logging.INFO, logger="ember.aux")

    response = aux_client.aux_chat(
        tag="COACHING_FILTER", step="semantic check", timeout_s=5,
        model="m", messages=[{"role": "user", "content": "hi"}],
        options={"temperature": 0, "num_predict": 10}, think=False,
    )
    server.shutdown()

    assert response["message"]["content"] == "YES"
    sent = _Answers.seen_bodies[0]
    assert sent["think"] is False
    assert sent["options"] == {"temperature": 0, "num_predict": 10}
    lines = [r.getMessage() for r in caplog.records]
    assert any(
        l.startswith("[COACHING_FILTER] semantic check ms=") and "outcome" not in l
        for l in lines
    )


def test_error_outcome_names_type_only(monkeypatch, caplog):
    server = _serve(_Fails)
    _point_at(monkeypatch, server)
    caplog.set_level(logging.INFO, logger="ember.aux")
    with pytest.raises(Exception):
        aux_client.aux_chat(
            tag="REVIEW", step="llm call", timeout_s=2,
            model="m", messages=[{"role": "user", "content": "x"}],
        )
    server.shutdown()
    line = next(r.getMessage() for r in caplog.records
                if r.getMessage().startswith("[REVIEW] llm call"))
    assert "boom" not in caplog.text
    assert "outcome=error" in line
    assert "type=" in line
