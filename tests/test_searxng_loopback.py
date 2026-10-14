"""
tests/test_searxng_loopback.py

docker-compose.yml publishes SearXNG on 127.0.0.1 only. "localhost" resolves
to ::1 first on Windows, which SearXNG does not listen on, and the refused
IPv6 connect costs ~2 s before the IPv4 fallback, on every web search and
every /api/health. Both clients now name 127.0.0.1, and the health probe
waits at most 1 s.
"""

from __future__ import annotations

import re
import socket
import time
from unittest.mock import MagicMock
from urllib.parse import urlsplit

import httpx
import pytest

import src.api.main as main
import src.tools.web_search as web_search_mod
from tests.conftest import REPO_ROOT

# Captured at import, before any test-wide stub of the health probe, so these
# tests always exercise the real function.
from src.api.main import _check_docker_status as real_check_docker_status


def test_web_search_requests_ipv4_loopback(monkeypatch):
    urls: list[str] = []

    def fake_get(url, *args, **kwargs):
        urls.append(url)
        return MagicMock(status_code=200, json=lambda: {"results": []})

    monkeypatch.setattr(web_search_mod.requests, "get", fake_get)

    web_search_mod.web_search("synthetic query")

    parts = urlsplit(urls[0])
    assert (parts.hostname, parts.port, parts.path) == ("127.0.0.1", 8888, "/search")


def test_searxng_url_matches_the_compose_port_binding():
    """The client must use the address compose publishes; this fails if
    either file changes without the other."""
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    host, port = re.search(r'"(\d+\.\d+\.\d+\.\d+):(\d+):\d+"', compose).groups()

    parts = urlsplit(web_search_mod.SEARXNG_BASE_URL)

    assert (parts.hostname, parts.port) == (host, int(port))


def test_health_probe_requests_ipv4_loopback_with_a_1s_timeout(monkeypatch):
    calls: list[tuple] = []

    def fake_get(url, *args, **kwargs):
        calls.append((url, kwargs.get("timeout")))
        return MagicMock(status_code=200)

    monkeypatch.setattr("httpx.get", fake_get)

    assert real_check_docker_status() == "ok"
    assert calls == [("http://127.0.0.1:8888/", 1.0)]


@pytest.fixture
def silent_listener():
    """A local TCP listener that accepts connections and never answers."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    yield f"http://127.0.0.1:{server.getsockname()[1]}"
    server.close()


def test_health_probe_gives_up_within_its_bound(monkeypatch, silent_listener):
    monkeypatch.setattr(main, "SEARXNG_BASE_URL", silent_listener)

    start = time.perf_counter()
    status = real_check_docker_status()
    elapsed = time.perf_counter() - start

    assert status == "down"
    assert elapsed < 2.0


def test_control_silent_listener_really_hangs(silent_listener):
    """Positive control: the listener makes a client wait out its timeout,
    so the fast "down" above comes from the 1 s bound, not a refused
    connection."""
    start = time.perf_counter()
    with pytest.raises(httpx.TimeoutException):
        httpx.get(f"{silent_listener}/", timeout=1.5)
    assert time.perf_counter() - start >= 1.4
