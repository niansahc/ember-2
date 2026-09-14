"""
tests/test_generation_host.py

Generation-only Ollama host: response generation can target a remote instance
while embeddings, vision, and every auxiliary LLM caller stay local.

The ollama package exposes one module-level client built at import, so
OLLAMA_HOST moves every caller at once. A feasibility check against a remote
box confirmed the consequence: generation worked, and the turn still died with
`model "nomic-embed-text" not found (404)` because the embedding call followed
it. These tests pin the split, and pin the unset default to today's behaviour.
"""

import os

import ollama
import pytest

import src.core.config as cfg
import src.llm.adapter as adapter


@pytest.fixture(autouse=True)
def clear_generation_host(monkeypatch):
    """Every test states its own host explicitly, and none leaks."""
    monkeypatch.delenv("EMBER_GENERATION_OLLAMA_HOST", raising=False)
    adapter._client_for_host.cache_clear()
    yield
    adapter._client_for_host.cache_clear()


REMOTE = "100.83.127.52:11434"


class TestUnsetIsTodaysBehaviour:
    """The boundary rule: unset config must leave a single-PC install alone."""

    def test_unset_returns_the_ollama_module_itself(self):
        # Not merely "a client pointing at localhost" -- the module, so the
        # default path runs the same call it ran before this setting existed.
        assert adapter._generation_client() is ollama

    def test_empty_string_is_treated_as_unset(self, monkeypatch):
        monkeypatch.setenv("EMBER_GENERATION_OLLAMA_HOST", "")
        assert adapter._generation_client() is ollama

    def test_patching_module_chat_still_intercepts_generation(self, monkeypatch):
        """Existing tests patch src.llm.adapter.ollama.chat. That must keep
        working, or the default path is not the same path."""
        calls = []
        monkeypatch.setattr(
            ollama, "chat", lambda **kw: calls.append(kw) or {"message": {"content": "x"}}
        )
        adapter._generation_client().chat(model="m", messages=[])
        assert len(calls) == 1


class TestConfiguredHost:
    def test_returns_client_bound_to_that_host(self, monkeypatch):
        monkeypatch.setenv("EMBER_GENERATION_OLLAMA_HOST", REMOTE)
        client = adapter._generation_client()

        assert isinstance(client, ollama.Client)
        assert str(client._client.base_url).rstrip("/") == f"http://{REMOTE}"

    def test_same_host_reuses_one_client(self, monkeypatch):
        monkeypatch.setenv("EMBER_GENERATION_OLLAMA_HOST", REMOTE)
        assert adapter._generation_client() is adapter._generation_client()

    def test_resolved_per_call_not_at_import(self, monkeypatch):
        """OLLAMA_HOST binds when the package is imported, which is why it
        needs a restart. This must not inherit that."""
        assert adapter._generation_client() is ollama

        monkeypatch.setenv("EMBER_GENERATION_OLLAMA_HOST", REMOTE)
        assert adapter._generation_client() is not ollama

        monkeypatch.delenv("EMBER_GENERATION_OLLAMA_HOST")
        assert adapter._generation_client() is ollama


class TestEverythingElseStaysLocal:
    """The regression that motivated the split."""

    def test_embeddings_do_not_follow_the_generation_host(self, monkeypatch):
        monkeypatch.setenv("EMBER_GENERATION_OLLAMA_HOST", REMOTE)

        # embed is bound to the package's own default client, not to whatever
        # generation resolved to.
        assert ollama.embed.__self__ is ollama._client
        assert adapter._generation_client() is not ollama._client

    def test_vision_service_uses_module_level_ollama(self, monkeypatch):
        monkeypatch.setenv("EMBER_GENERATION_OLLAMA_HOST", REMOTE)
        source = _read_source("src", "llm", "vision_service.py")
        assert "ollama.chat(" in source
        assert "_generation_client" not in source

    def test_auxiliary_callers_do_not_use_the_generation_client(self):
        """Intent classifier, coaching filter, guardrail, reflection, state
        extractor, deviation detector. Which callers count as "generation" is a
        later decision; today only the two response-generation sites move."""
        for parts in (
            ("src", "llm", "intent_classifier.py"),
            ("src", "llm", "coaching_filter.py"),
            ("src", "llm", "prompt_guardrail.py"),
            ("src", "reflection", "generate_reflection.py"),
            ("src", "state", "state_extractor.py"),
            ("src", "safety", "deviation_detector.py"),
        ):
            assert "_generation_client" not in _read_source(*parts), parts

    def test_only_the_two_generation_sites_moved(self):
        source = _read_source("src", "llm", "adapter.py")
        # _summarize_with_plain_prompt and _call_model_with_prompt stay on the
        # default client: both call ollama.chat with no model= at all, so
        # neither depends on the pinned model.
        assert source.count("_generation_client().chat(") == 2
        assert source.count("ollama.chat(") == 2


class TestLocalBaseUrlMatchesTheLibrary:
    """get_ollama_base_url() exists only because grounding_check speaks raw
    HTTP. If it drifts from the library's resolution, that caller silently
    talks to a different instance than everything else -- the exact class of
    bug this change is removing."""

    @pytest.mark.parametrize(
        "host",
        [
            None,
            "",
            "1.2.3.4",
            ":56789",
            ":11434",
            "1.2.3.4:56789",
            "http://1.2.3.4",
            "https://1.2.3.4",
            "https://1.2.3.4:56789",
            "example.com",
            "example.com:11434",
            "http://example.com/",
            "localhost:11434",
            "100.83.127.52:11434",
        ],
    )
    def test_agrees_with_ollama_parse_host(self, host, monkeypatch):
        from ollama._client import _parse_host

        if host is None:
            monkeypatch.delenv("OLLAMA_HOST", raising=False)
        else:
            monkeypatch.setenv("OLLAMA_HOST", host)

        assert cfg.get_ollama_base_url() == _parse_host(host or None)

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        assert cfg.get_ollama_base_url() == "http://127.0.0.1:11434"


class TestGroundingCheckHasNoHardcodedHost:
    """Grep guard, matching the convention in tests/test_eval_helpers.py."""

    def test_no_localhost_literal(self):
        source = _read_source("src", "safety", "grounding_check.py")
        assert "localhost:11434" not in source
        assert "get_ollama_base_url()" in source

    def test_grounding_url_follows_ollama_host(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_HOST", "1.2.3.4:9999")
        assert cfg.get_ollama_base_url() == "http://1.2.3.4:9999"

    def test_grounding_ignores_the_generation_host(self, monkeypatch):
        """Generation moving must not drag the grounding check with it."""
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
        monkeypatch.setenv("EMBER_GENERATION_OLLAMA_HOST", REMOTE)
        assert cfg.get_ollama_base_url() == "http://127.0.0.1:11434"


def _read_source(*parts) -> str:
    from pathlib import Path

    return Path(__file__).resolve().parents[1].joinpath(*parts).read_text(encoding="utf-8")
