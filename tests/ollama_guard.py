"""
tests/ollama_guard.py

No test reaches the Ollama server (port 11434). Loaded from tests/conftest.py
through pytest_plugins, so it is active before collection imports anything.

Two layers:

  1. Model stub. Every httpx request to port 11434, on any host, raises
     httpx.ConnectError before a socket opens. The ollama client (sync and
     async) and grounding_check's httpx.AsyncClient both go through httpx,
     so every test sees what CI sees: Ollama unreachable, at once. A test
     that needs a model answer installs its own fake, which takes precedence.

  2. Guard. Anything that bypasses httpx (requests, urllib, a raw socket)
     and opens a connection to port 11434 is refused with
     OllamaConnectionBlocked and recorded, and the test fails, even when the
     code under test swallowed the exception (Stage 3 catches everything).

Tests marked live_ollama are the exception: both layers stand down for them.
They are deselected by default (tests/pytest.ini addopts) and run with
`pytest -m live_ollama` against a running Ollama.

Limit: child processes (tests/conftest.py::run_fresh_python) do not load this
plugin, so a connection made in a child is neither stubbed nor caught.

This file was written to fix a slow suite. With qwen3:8b installed, about 140
tests reached the live model and the run went from about 3 to 18 minutes,
leaving unclosed sockets to 127.0.0.1:11434 behind.
"""

from __future__ import annotations

import socket
import threading

import httpx
import pytest

OLLAMA_PORT = 11434


class OllamaConnectionBlocked(ConnectionRefusedError):
    """A test tried to open a connection to the Ollama port."""


_lock = threading.Lock()
_violations: list[str] = []
_allowed = False  # True while a live_ollama test runs


def _blocked(port) -> bool:
    """True when a request or connection to `port` must not go through."""
    if _allowed or port is None:
        return False
    try:
        return int(port) == OLLAMA_PORT
    except (TypeError, ValueError):
        return False


def _record(what: str) -> None:
    with _lock:
        _violations.append(what)


def take_violations() -> list[str]:
    """Return and clear the recorded connection attempts."""
    with _lock:
        taken = list(_violations)
        _violations.clear()
    return taken


def is_allowed() -> bool:
    """True while a live_ollama test runs."""
    return _allowed


# --- Layer 1: model stub at the httpx transport ----------------------------

_orig_handle_request = httpx.HTTPTransport.handle_request
_orig_handle_async_request = httpx.AsyncHTTPTransport.handle_async_request


def _stubbed_error(request: httpx.Request) -> httpx.ConnectError:
    return httpx.ConnectError(
        "Ollama is stubbed in tests (tests/ollama_guard.py)", request=request
    )


def _handle_request(self, request):
    if _blocked(request.url.port):
        raise _stubbed_error(request)
    return _orig_handle_request(self, request)


async def _handle_async_request(self, request):
    if _blocked(request.url.port):
        raise _stubbed_error(request)
    return await _orig_handle_async_request(self, request)


httpx.HTTPTransport.handle_request = _handle_request
httpx.AsyncHTTPTransport.handle_async_request = _handle_async_request


# --- Layer 2: socket guard ---------------------------------------------------
# getaddrinfo is the first step of socket.create_connection (requests, urllib)
# and of asyncio's create_connection, including the Windows proactor loop,
# which connects without calling socket.connect. connect/connect_ex catch a
# socket aimed at a literal address.

_orig_getaddrinfo = socket.getaddrinfo


def _refuse(host, port):
    what = f"{host}:{port}"
    _record(what)
    raise OllamaConnectionBlocked(f"connection to Ollama port refused in tests: {what}")


def _getaddrinfo(host, port, *args, **kwargs):
    if _blocked(port):
        _refuse(host, port)
    return _orig_getaddrinfo(host, port, *args, **kwargs)


def _guard_connect(orig):
    """Wrap socket.connect / connect_ex. An AF_UNIX address is a str, not a
    (host, port) tuple, and is never blocked."""
    def connect(self, address):
        if isinstance(address, tuple) and len(address) >= 2 and _blocked(address[1]):
            _refuse(address[0], address[1])
        return orig(self, address)
    return connect


socket.getaddrinfo = _getaddrinfo
socket.socket.connect = _guard_connect(socket.socket.connect)
socket.socket.connect_ex = _guard_connect(socket.socket.connect_ex)


# --- pytest hooks ------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "live_ollama: reaches a running Ollama; deselected by default, "
        "run with -m live_ollama (tests/ollama_guard.py)",
    )


def _fail_on_violations(phase: str) -> None:
    taken = take_violations()
    if taken:
        pytest.fail(
            f"opened a connection to Ollama port {OLLAMA_PORT} during {phase} "
            f"({len(taken)} attempt(s): {', '.join(sorted(set(taken)))}). "
            "Stub the model, or mark the test live_ollama. See tests/ollama_guard.py.",
            pytrace=False,
        )


def pytest_runtest_setup(item):
    global _allowed
    take_violations()
    _allowed = item.get_closest_marker("live_ollama") is not None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item):
    try:
        result = yield
    except BaseException:
        take_violations()
        raise
    _fail_on_violations("the test")
    return result


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    global _allowed
    try:
        result = yield
    finally:
        _allowed = False
    _fail_on_violations("teardown")
    return result
