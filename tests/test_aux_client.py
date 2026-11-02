"""
tests/test_aux_client.py

Auxiliary Ollama calls on the request path carry a real client timeout, log
their duration, and pass their kwargs through untouched.

These tests use the real ollama client against a local stub HTTP server, not
the conftest shim, so the httpx timeout itself is what is under test.
"""

import json
import logging
import re
import select
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from src.llm import aux_client, intent_classifier

pytestmark = pytest.mark.real_aux_client


@pytest.fixture(autouse=True)
def fresh_clients():
    """Each test sets its own OLLAMA_HOST, read when a client is built."""
    aux_client._client_for_timeout.cache_clear()
    yield
    aux_client._client_for_timeout.cache_clear()


@pytest.fixture
def serve():
    """Start a stub server for a handler. Closed at teardown, so a failed
    assert cannot leave one running for the rest of the session."""
    servers = []

    def start(handler_cls):
        # Threading, so shutdown() does not wait out a hung handler.
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


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


def test_hung_server_raises_timeout_and_logs_outcome(serve, monkeypatch, caplog):
    _point_at(monkeypatch, serve(_Hangs))
    caplog.set_level(logging.INFO, logger="ember.aux")

    start = time.perf_counter()
    with pytest.raises(httpx.TimeoutException):
        aux_client.aux_chat(
            tag="COACHING_FILTER", step="semantic check", timeout_s=0.5,
            model="m", messages=[{"role": "user", "content": "SECRET-TEXT"}],
        )
    elapsed = time.perf_counter() - start

    assert elapsed < 3.0
    line = next(r.getMessage() for r in caplog.records
                if r.getMessage().startswith("[COACHING_FILTER] semantic check"))
    assert "outcome=timeout" in line
    assert "ms=" in line
    assert "SECRET-TEXT" not in caplog.text


def test_answering_server_returns_result_logs_ms_and_passes_kwargs(serve, monkeypatch, caplog):
    """Positive control for the timeout test: the same path succeeds when the
    server answers, so the timeout test cannot pass by the call failing for
    some other reason."""
    _Answers.seen_bodies = []
    _point_at(monkeypatch, serve(_Answers))
    caplog.set_level(logging.INFO, logger="ember.aux")

    response = aux_client.aux_chat(
        tag="COACHING_FILTER", step="semantic check", timeout_s=5,
        model="m", messages=[{"role": "user", "content": "hi"}],
        options={"temperature": 0, "num_predict": 10}, think=False,
    )

    assert response["message"]["content"] == "YES"
    sent = _Answers.seen_bodies[0]
    assert sent["think"] is False
    assert sent["options"] == {"temperature": 0, "num_predict": 10}
    lines = [r.getMessage() for r in caplog.records]
    assert any(
        l.startswith("[COACHING_FILTER] semantic check ms=") and "outcome" not in l
        for l in lines
    )


def test_error_outcome_names_type_only(serve, monkeypatch, caplog):
    _point_at(monkeypatch, serve(_Fails))
    caplog.set_level(logging.INFO, logger="ember.aux")
    with pytest.raises(Exception):
        aux_client.aux_chat(
            tag="REVIEW", step="llm call", timeout_s=2,
            model="m", messages=[{"role": "user", "content": "x"}],
        )
    line = next(r.getMessage() for r in caplog.records
                if r.getMessage().startswith("[REVIEW] llm call"))
    assert "boom" not in caplog.text
    assert "outcome=error" in line
    assert "type=" in line


def _reach_stage3(monkeypatch, caplog, *, wait_ms, client_ms):
    """The next classify_intent reaches Stage 3 with these two timers."""
    monkeypatch.setattr(
        intent_classifier, "get_intent_classifier_timeout_ms", lambda: wait_ms)
    monkeypatch.setattr(
        intent_classifier, "_stage3_client_timeout_s", lambda: client_ms / 1000.0)
    monkeypatch.setattr(intent_classifier, "_stage1_classify", lambda q: None)
    monkeypatch.setattr(intent_classifier, "_stage2_classify", lambda q: (None, 0.1))
    monkeypatch.setenv("EMBER_CLASSIFIER_TELEMETRY", "true")
    caplog.set_level(logging.INFO)


def _finish_stage3():
    """Wait for the abandoned Stage 3 call."""
    for worker in threading.enumerate():
        if worker.name.startswith("ThreadPoolExecutor"):
            worker.join(timeout=5)


def _stage3_overrun(serve, monkeypatch, caplog, *, wait_ms, client_ms):
    """classify_intent against a hung server, with the order of the two timers
    chosen by the test. Returns the telemetry stages, the failure warnings and
    the aux_chat outcome lines, once the abandoned call has finished."""
    _point_at(monkeypatch, serve(_Hangs))
    _reach_stage3(monkeypatch, caplog, wait_ms=wait_ms, client_ms=client_ms)

    label = intent_classifier.classify_intent("synthetic stalled query")
    _finish_stage3()

    messages = [r.getMessage() for r in caplog.records]
    assert label == intent_classifier.VAULT_ANSWERABLE
    return (
        [m.group(1) for m in map(re.compile(r"stage=(\S+) label=").search, messages) if m],
        [m for m in messages if "Stage 3 failed" in m],
        [m for m in messages if m.startswith("[INTENT_CLASSIFY] stage3 outcome=")],
    )


@pytest.mark.parametrize("wait_ms, client_ms", [
    pytest.param(100, 600, id="budget-wait-fires-first"),
    # Margin for building the client, which can take a second under load.
    pytest.param(3000, 100, id="client-timeout-fires-first"),
    *[pytest.param(150, 150, id=f"same-deadline-{i}") for i in range(5)],
])
def test_a_stage3_overrun_is_one_outcome_whichever_timer_fires_first(
    serve, monkeypatch, caplog, wait_ms, client_ms,
):
    """The telemetry stage is SetFit training data (ADR-034), so it must not
    depend on which timer won. The client outlasts the budget in production;
    the other orders stay pinned in case that ever stops holding."""
    stages, warnings, aux_lines = _stage3_overrun(
        serve, monkeypatch, caplog, wait_ms=wait_ms, client_ms=client_ms)

    assert stages == ["timeout"]
    assert len(warnings) == 1 and "TimeoutError" in warnings[0]
    assert len(aux_lines) == 1 and "outcome=timeout" in aux_lines[0]


def test_positive_control_an_unmapped_client_timeout_reads_as_a_failure(
    serve, monkeypatch, caplog,
):
    """Without the mapping the client-first order logs stage3_failed, so the
    test above can see the split it exists to prevent."""
    monkeypatch.setattr(intent_classifier, "_OVERRUN_ERRORS", ())
    stages, warnings, _ = _stage3_overrun(
        serve, monkeypatch, caplog, wait_ms=3000, client_ms=100)

    assert stages == ["stage3_failed"]
    assert "ReadTimeout" in warnings[0]


class _SlowLoad(BaseHTTPRequestHandler):
    """Answers late, like Ollama loading a model, and notes whether the client
    was still connected at the end: Ollama aborts the load if it is not."""

    finished = threading.Event()
    client_left: bool | None = None

    def do_POST(self):  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        time.sleep(1.0)
        readable, _, _ = select.select([self.connection], [], [], 0)
        try:
            gone = bool(readable) and self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            gone = True
        _SlowLoad.client_left = gone
        _SlowLoad.finished.set()
        if gone:
            return
        body = json.dumps({
            "model": "m",
            "message": {"role": "assistant", "content": '{"label": "vault_answerable"}'},
            "done": True,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _stage3_during_a_load(serve, monkeypatch, caplog, *, client_ms):
    _SlowLoad.finished = threading.Event()
    _SlowLoad.client_left = None
    _point_at(monkeypatch, serve(_SlowLoad))
    _reach_stage3(monkeypatch, caplog, wait_ms=200, client_ms=client_ms)

    start = time.perf_counter()
    intent_classifier.classify_intent("synthetic cold query")
    elapsed = time.perf_counter() - start
    assert _SlowLoad.finished.wait(5)
    _finish_stage3()
    return elapsed, [r.getMessage() for r in caplog.records]


def test_stage3_overrun_leaves_the_model_load_running(serve, monkeypatch, caplog):
    """The budget still caps the turn, and the client stays connected past it,
    so Ollama finishes the load for the next query."""
    elapsed, messages = _stage3_during_a_load(serve, monkeypatch, caplog, client_ms=3000)

    assert elapsed < 0.9
    assert _SlowLoad.client_left is False
    assert any(m.startswith("[INTENT_CLASSIFY] stage3 ms=") for m in messages)
    assert any("stage=timeout label=" in m for m in messages)


def test_positive_control_a_client_timeout_at_the_budget_drops_the_load(
    serve, monkeypatch, caplog,
):
    _, messages = _stage3_during_a_load(serve, monkeypatch, caplog, client_ms=200)

    assert _SlowLoad.client_left is True
    assert any(m.startswith("[INTENT_CLASSIFY] stage3 outcome=timeout") for m in messages)
