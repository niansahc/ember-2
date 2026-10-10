"""
tests/test_auth_scope_path.py

Security middleware reads the path from request.scope, never request.url.
request.url is rebuilt from the Host header, so a crafted Host could make
the URL path look like a public route (CVE-2026-48710, Starlette < 1.3.1).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import UI_FIXTURE_API_KEY as KEY

BAD_HOST = {"Host": "127.0.0.1:8000/api/health?x="}


@pytest.fixture
def client(loopback_keyed_client):
    return loopback_keyed_client


def test_middleware_ignores_request_url(client: TestClient):
    """request.url is made to lie (its path says a public route) while the
    request line asks for a keyed route. The gate must still answer 401. This
    holds on any Starlette version, patched or not, because the middleware
    never consults request.url."""
    from starlette.datastructures import URL
    from starlette.requests import Request

    lying_url = property(lambda self: URL("http://127.0.0.1:8000/api/health"))
    with patch.object(Request, "url", lying_url):
        response = client.get("/v1/models")
    assert response.status_code == 401

    # Positive control: a gate that read request.url would be fooled.
    import src.api.main as main_module

    with patch.object(Request, "url", lying_url), \
         patch.object(main_module, "_scope_path", lambda request: request.url.path):
        response = client.get("/v1/models")
    assert response.status_code != 401


def test_bad_host_cannot_reach_keyed_route_without_key(client: TestClient):
    """Two layers: the host allowlist answers 421 first; with that layer
    opened the key gate reads the scope path and answers 401."""
    import src.api.main as main_module

    response = client.get("/v1/models", headers=BAD_HOST)
    assert response.status_code == 421

    with patch.object(main_module, "_allowed_hosts", return_value=frozenset({BAD_HOST["Host"]})):
        response = client.get("/v1/models", headers=BAD_HOST)
    assert response.status_code == 401


def test_valid_key_and_host_reach_the_route(client: TestClient):
    """Positive control for the gate the bad-host test exercises."""
    response = client.get("/v1/models", headers={"X-API-Key": KEY})
    assert response.status_code != 401


def test_starlette_floor_installed():
    import starlette

    installed = tuple(int(x) for x in starlette.__version__.split(".")[:3])
    assert installed >= (1, 3, 1)
