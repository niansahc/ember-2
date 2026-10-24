"""
tests/test_searxng_loopback.py

Web search and the /api/health probe reach SearXNG on IPv4 loopback
(SEARXNG_BASE_URL in src/core/config.py explains why not "localhost"), and
the health probe's timeout is 1 s per phase (httpx timeouts are per phase,
not a total deadline). The probe's exact URL and timeout are pinned in
tests/test_service_endpoints.py TestHealthCheckDocker.
"""

from __future__ import annotations

import socket
import time
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import httpx
import pytest
import yaml

import src.api.main as main
import src.tools.web_search as web_search_mod
from src.core.config import SEARXNG_BASE_URL
from tests.conftest import REPO_ROOT


def test_web_search_requests_ipv4_loopback():
    response = MagicMock(status_code=200, json=lambda: {"results": []})
    with patch.object(web_search_mod.requests, "get", return_value=response) as get:
        web_search_mod.web_search("synthetic query")

    parts = urlsplit(get.call_args.args[0])
    assert (parts.hostname, parts.port, parts.path) == ("127.0.0.1", 8888, "/search")


def test_searxng_url_matches_the_compose_port_binding():
    """The client must use the address compose publishes; this fails if
    either file changes without the other."""
    compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    host, port, _ = compose["services"]["searxng"]["ports"][0].split(":")

    parts = urlsplit(SEARXNG_BASE_URL)

    assert (parts.hostname, parts.port) == (host, int(port))


def test_searxng_url_is_plain_http():
    """The health probe passes verify=False (src/api/main.py
    _check_docker_status), which is safe only while this URL is plain http
    on loopback. An https URL must get certificate checks back."""
    assert urlsplit(SEARXNG_BASE_URL).scheme == "http"


@pytest.fixture
def silent_listener():
    """A local TCP listener that accepts connections and never answers."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    yield f"http://127.0.0.1:{server.getsockname()[1]}"
    server.close()


@pytest.mark.docker_probe
def test_health_probe_gives_up_within_its_bound(monkeypatch, silent_listener):
    monkeypatch.setattr(main, "SEARXNG_BASE_URL", silent_listener)

    start = time.perf_counter()
    status = main._check_docker_status()
    elapsed = time.perf_counter() - start

    assert status == "down"
    # Lower bound: the 1 s timeout ended the wait, not a refused connection.
    assert 0.9 <= elapsed < 2.0


def test_control_silent_listener_really_hangs(silent_listener):
    """Positive control: the listener makes a client wait out its timeout,
    so the "down" above comes from the 1 s timeout, not a refused
    connection."""
    start = time.perf_counter()
    with pytest.raises(httpx.TimeoutException):
        httpx.get(f"{silent_listener}/", timeout=0.3)
    assert time.perf_counter() - start >= 0.25
