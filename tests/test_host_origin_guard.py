"""
tests/test_host_origin_guard.py

host_origin_guard: 421 for a Host this server does not answer for (DNS
rebinding defence), 403 for state-changing requests a browser marks
cross-site or sends from a foreign Origin. Loopback on the listening port is
always accepted; EMBER_ALLOWED_HOSTS adds, never replaces.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import LOOPBACK_BASE_URL, UI_FIXTURE_API_KEY as KEY, ui_client

EXTRA_HOST = "ember.example.ts.net"


@pytest.fixture
def client(loopback_keyed_client):
    return loopback_keyed_client


@pytest.fixture
def client_with_extra_host(ui_tree, monkeypatch):
    monkeypatch.setenv("EMBER_ALLOWED_HOSTS", f" {EXTRA_HOST} , other.example:9000")
    with ui_client(ui_tree, api_key=KEY, base_url=LOOPBACK_BASE_URL) as c:
        yield c


# --- Host allowlist ---------------------------------------------------------

def test_loopback_hosts_accepted(client: TestClient):
    for host in ("127.0.0.1:8000", "localhost:8000", "LOCALHOST:8000"):
        response = client.get("/api/health", headers={"Host": host})
        assert response.status_code == 200, host


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "evil.example:8000",
        "127.0.0.1:9999",
        "127.0.0.1",
        "127.0.0.1:8000/api/health?x=",
        "",
    ],
)
def test_foreign_host_is_421_even_on_public_route(client: TestClient, host: str):
    response = client.get("/api/health", headers={"Host": host})
    assert response.status_code == 421, host
    response = client.get("/v1/models", headers={"Host": host, "X-API-Key": KEY})
    assert response.status_code == 421, host


def test_extra_host_without_env_is_rejected(client: TestClient):
    """Positive control for the env var test: unset, the extra host is foreign."""
    response = client.get("/api/health", headers={"Host": EXTRA_HOST})
    assert response.status_code == 421


def test_env_var_host_accepted(client_with_extra_host: TestClient):
    for host in (EXTRA_HOST, EXTRA_HOST.upper(), "other.example:9000"):
        response = client_with_extra_host.get("/api/health", headers={"Host": host})
        assert response.status_code == 200, host


def test_loopback_survives_env_var(client_with_extra_host: TestClient):
    for host in ("127.0.0.1:8000", "localhost:8000"):
        response = client_with_extra_host.get("/api/health", headers={"Host": host})
        assert response.status_code == 200, host
    response = client_with_extra_host.get("/api/health", headers={"Host": "evil.example"})
    assert response.status_code == 421


def test_bare_loopback_accepted_on_default_port(ui_tree, monkeypatch):
    """Browsers omit :80 and :443; the bare names are allowed on those ports."""
    monkeypatch.delenv("EMBER_ALLOWED_HOSTS", raising=False)
    with ui_client(ui_tree, api_key=KEY, base_url="http://localhost") as c:
        assert c.get("/api/health", headers={"Host": "localhost"}).status_code == 200
        assert c.get("/api/health", headers={"Host": "127.0.0.1"}).status_code == 200
        assert c.get("/api/health", headers={"Host": "localhost:8000"}).status_code == 421


# --- Origin check -----------------------------------------------------------

POST_PATH = "/v1/security/pin/verify"  # public, so the result isolates the guard


def test_cross_site_post_is_403(client: TestClient):
    response = client.post(POST_PATH, json={}, headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403


@pytest.mark.parametrize("origin", ["https://evil.example", "http://127.0.0.1:9999", "null", "not a url"])
def test_foreign_origin_post_is_403(client: TestClient, origin: str):
    response = client.post(POST_PATH, json={}, headers={"Origin": origin})
    assert response.status_code == 403, origin


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_other_state_changing_methods_are_guarded(client: TestClient, method: str):
    response = client.request(
        method, "/v1/preferences", headers={"Sec-Fetch-Site": "cross-site", "X-API-Key": KEY}
    )
    assert response.status_code == 403, method


def test_same_origin_post_passes(client: TestClient):
    for headers in (
        {"Origin": "http://127.0.0.1:8000"},
        {"Origin": "http://localhost:8000", "Host": "localhost:8000"},
        {"Sec-Fetch-Site": "same-origin"},
        {"Sec-Fetch-Site": "same-origin", "Origin": "http://127.0.0.1:8000"},
        {},  # non-browser API client
    ):
        response = client.post(POST_PATH, json={}, headers=headers)
        assert response.status_code not in (403, 421), headers


def test_env_var_origin_accepted(client_with_extra_host: TestClient):
    response = client_with_extra_host.post(
        POST_PATH, json={}, headers={"Host": EXTRA_HOST, "Origin": f"https://{EXTRA_HOST}"}
    )
    assert response.status_code not in (403, 421)


def test_cross_site_get_is_not_blocked(client: TestClient):
    """Only state-changing methods are guarded; a cross-site GET reaches auth."""
    response = client.get(
        "/api/health", headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}
    )
    assert response.status_code == 200


# --- Positive controls with a valid key --------------------------------------
# GET with a valid key is covered in test_auth_scope_path.py on the same client.

def test_keyed_post_with_valid_key_and_same_origin(client: TestClient):
    response = client.post(
        "/v1/chat/completions",
        json={"model": "x", "messages": []},
        headers={"X-API-Key": KEY, "Origin": "http://127.0.0.1:8000", "Sec-Fetch-Site": "same-origin"},
    )
    assert response.status_code not in (401, 403, 421)


def test_keyed_post_cross_site_still_403_with_valid_key(client: TestClient):
    """The key does not override the origin check: a rebinding page cannot
    use a key it somehow holds from a cross-site context."""
    response = client.post(
        "/v1/chat/completions",
        json={"model": "x", "messages": []},
        headers={"X-API-Key": KEY, "Sec-Fetch-Site": "cross-site"},
    )
    assert response.status_code == 403
