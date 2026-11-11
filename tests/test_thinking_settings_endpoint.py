"""
tests/test_thinking_settings_endpoint.py

The Thinking setting (ADR-049) at the API: the chat handler reads it once
per request and hands it to generation, and GET/POST /v1/settings/thinking
read and set it for the UI. Ollama and the preference store are mocked;
synthetic data only.
"""

from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

import pytest

from src.core import preferences
from tests.test_generation_failure_stream import _post_chat


@pytest.fixture
def client(keyless_client):
    return keyless_client


def _stored(monkeypatch, value: bool) -> list:
    """The resolver reports `value`; returns the list of its calls. The
    resolver itself is covered in test_preferences_thinking.py."""
    calls: list = []

    def resolver(vault_path=None):
        calls.append(vault_path)
        return value

    monkeypatch.setattr(preferences, "generation_thinking_enabled", resolver)
    return calls


# --- the chat handler: one read per request, passed to generation ------------

def _chat_kwargs(client, *, stream: bool) -> dict:
    """POST one turn; return the kwargs generation received."""
    from src.api import openai_adapter as oa

    seen: dict = {}

    def fake_iter(_packet, **kwargs):
        seen.update(kwargs)
        yield "A grounded reply."

    def fake_generate(_packet, **kwargs):
        seen.update(kwargs)
        return "A grounded reply."

    response = _post_chat(client, stream=stream, extra_patches=(
        patch.object(oa.llm_adapter, "generate_response_iter", side_effect=fake_iter),
        patch.object(oa.llm_adapter, "generate_response", side_effect=fake_generate),
        patch("src.safety.grounding_check.run_grounding_check", return_value=(True, None)),
    ))
    assert response.status_code == 200
    return seen


@pytest.mark.parametrize("stream", [True, False])
def test_stored_on_reaches_generation_as_thinking_true(client, monkeypatch, stream):
    resolver_calls = _stored(monkeypatch, True)
    assert _chat_kwargs(client, stream=stream)["thinking"] is True
    assert len(resolver_calls) == 1


@pytest.mark.parametrize("stream", [True, False])
def test_stored_off_reaches_generation_as_thinking_false(client, monkeypatch, stream):
    """Control for the test above: only the stored value differs."""
    resolver_calls = _stored(monkeypatch, False)
    assert _chat_kwargs(client, stream=stream)["thinking"] is False
    assert len(resolver_calls) == 1


# --- GET and POST /v1/settings/thinking --------------------------------------

_PATH = "/v1/settings/thinking"


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Point the preference store at an empty temp vault."""
    monkeypatch.setattr(preferences, "get_private_vault_path", lambda: tmp_path)
    return tmp_path


class _Show:
    def __init__(self, capabilities):
        self.capabilities = capabilities


@contextmanager
def _on_model(model="qwen3:8b", capabilities=None, error=None):
    """Make `model` the active model; its capability lookup returns
    `capabilities` or raises `error`. Yields the models looked up."""
    from src.api import main

    shown: list = []

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def show(self, name):
            shown.append(name)
            if error is not None:
                raise error
            return _Show(capabilities)

    with patch("src.llm.adapter.ollama.Client", _Client), \
            patch.object(main.llm_adapter, "model", model):
        yield shown


def _get(client, **model):
    with _on_model(**model) as shown:
        response = client.get(_PATH)
    return response, shown


def test_get_reports_a_thinking_model_as_supported(client, vault):
    response, shown = _get(client, capabilities=["completion", "thinking"])
    assert response.status_code == 200
    assert response.json() == {
        "enabled": False, "model": "qwen3:8b", "supported": True, "cloud": False}
    assert shown == ["qwen3:8b"]


def test_get_reports_a_model_without_thinking_as_unsupported(client, vault):
    response, _ = _get(client, model="llama3.2:1b", capabilities=["completion", "tools"])
    assert response.json()["supported"] is False


def test_get_reports_unknown_when_the_lookup_fails(client, vault):
    response, _ = _get(client, error=ConnectionError("down"))
    assert response.status_code == 200
    assert response.json()["supported"] is None


def test_get_on_a_cloud_model_skips_the_lookup(client, vault):
    """Absence test (no lookup); test_get_reports_a_thinking_model_as_supported
    is its positive control (the lookup runs for an Ollama model)."""
    response, shown = _get(client, model="claude-sonnet-5-5", capabilities=["thinking"])
    assert response.json() == {
        "enabled": False, "model": "claude-sonnet-5-5", "supported": False, "cloud": True}
    assert shown == []


def test_post_round_trips(client, vault):
    with _on_model(capabilities=["thinking"]):
        on = client.post(_PATH, json={"enabled": True})
        assert on.status_code == 200
        assert on.json()["enabled"] is True
        assert client.get(_PATH).json()["enabled"] is True
        off = client.post(_PATH, json={"enabled": False})
        assert off.json() == {
            "enabled": False, "model": "qwen3:8b", "supported": True, "cloud": False}
    assert preferences.generation_thinking_enabled(vault) is False


@pytest.mark.parametrize("body", [{}, {"enabled": None}, {"enabled": "true"},
                                  {"enabled": 1}, {"enabled": [True]}])
def test_post_rejects_anything_but_a_boolean(client, vault, body):
    """Absence test (stored value unchanged); test_post_round_trips is its
    positive control."""
    preferences.update({"generation_think": True}, vault_path=vault)
    response = client.post(_PATH, json=body)
    assert response.status_code == 400
    assert response.json() == {"detail": "enabled must be true or false"}
    assert preferences.generation_thinking_enabled(vault) is True


def test_post_body_that_is_not_an_object_is_422(client, vault):
    assert client.post(_PATH, json=[True]).status_code == 422


def test_post_reports_a_failed_write(client, vault):
    from src.core.jsonio import JsonIoError

    with patch("src.core.preferences.safe_write_json", side_effect=JsonIoError("x")):
        response = client.post(_PATH, json={"enabled": True})
    assert response.status_code == 500
    assert response.json() == {"detail": "Could not save the thinking setting"}
