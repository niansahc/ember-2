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


class TestModelEndpointReportsGenerationModels:
    """GET /model must let a caller see what generation can actually reach.

    `available` is built from the default client. Under a split config those
    are the models generation CANNOT use, which is what made the eval harness
    reject every remote candidate.
    """

    LOCAL = {"models": [{"model": "qwen3:8b"}, {"model": "nomic-embed-text:latest"}]}
    REMOTE = {"models": [{"model": "qwen3:32b"}, {"model": "gemma4:26b-a4b-it-q4_K_M"}]}

    def test_unset_response_has_exactly_todays_keys(self, monkeypatch):
        """The byte-identical claim, asserted rather than argued."""
        import src.api.main as main

        monkeypatch.setattr(main.ollama, "list", lambda: self.LOCAL)
        result = main.get_model_endpoint()

        assert set(result) == {"model", "available", "cloud", "reference_model", "pinned"}
        assert "generation_available" not in result
        assert "generation_host" not in result

    def test_configured_host_reports_its_own_models(self, monkeypatch):
        import src.api.main as main
        import src.llm.adapter as adapter

        monkeypatch.setenv("EMBER_GENERATION_OLLAMA_HOST", REMOTE)
        monkeypatch.setattr(main.ollama, "list", lambda: self.LOCAL)
        monkeypatch.setattr(adapter, "list_generation_models",
                            lambda: [m["model"] for m in self.REMOTE["models"]])

        result = main.get_model_endpoint()

        # The two lists are deliberately disjoint, so neither assertion can
        # pass by coincidence.
        assert result["generation_available"] == ["qwen3:32b", "gemma4:26b-a4b-it-q4_K_M"]
        assert result["available"] == ["qwen3:8b"]
        assert result["generation_host"] == REMOTE

    def test_embedding_filter_applies_to_the_generation_list_too(self, monkeypatch):
        import src.api.main as main
        import src.llm.adapter as adapter

        monkeypatch.setenv("EMBER_GENERATION_OLLAMA_HOST", REMOTE)
        monkeypatch.setattr(main.ollama, "list", lambda: self.LOCAL)
        monkeypatch.setattr(adapter, "list_generation_models",
                            lambda: ["qwen3:32b", "nomic-embed-text:latest"])

        result = main.get_model_endpoint()
        assert result["generation_available"] == ["qwen3:32b"]

    def test_unreachable_generation_host_yields_an_empty_list(self, monkeypatch):
        """Fail soft: pin_model treats empty as inconclusive, not as rejected,
        so an unreachable host does not hard-fail a sweep."""
        import src.api.main as main
        import src.llm.adapter as adapter

        monkeypatch.setenv("EMBER_GENERATION_OLLAMA_HOST", REMOTE)
        monkeypatch.setattr(main.ollama, "list", lambda: self.LOCAL)
        monkeypatch.setattr(adapter, "list_generation_models", lambda: [])

        assert main.get_model_endpoint()["generation_available"] == []

    def test_list_generation_models_returns_empty_on_failure(self, monkeypatch):
        import src.llm.adapter as adapter

        def _boom():
            raise ConnectionError("host unreachable")

        monkeypatch.setattr(adapter, "_generation_client",
                            lambda: type("C", (), {"list": staticmethod(_boom)})())
        assert adapter.list_generation_models() == []


class TestHarnessValidatesAgainstGenerationModels:
    """The rejection this change removes."""

    def test_remote_candidate_is_accepted_when_generation_list_is_present(self):
        from tools.eval_helpers import _installed_and_cloud_models

        state = {
            "available": ["qwen3:8b", "qwen3:4b"],          # local, cannot generate
            "generation_available": ["qwen3:32b"],           # what generation reaches
            "cloud": {},
        }
        names = _installed_and_cloud_models(state)

        assert "qwen3:32b" in names, "the remote candidate must validate"
        assert "qwen3:8b" not in names, "the local list must not mask it"

    def test_falls_back_to_available_when_absent(self):
        """Unset host, and servers that predate this change."""
        from tools.eval_helpers import _installed_and_cloud_models

        names = _installed_and_cloud_models({"available": ["qwen3:8b"], "cloud": {}})
        assert names == {"qwen3:8b"}

    def test_cloud_ids_still_merge_in(self):
        from tools.eval_helpers import _installed_and_cloud_models

        state = {
            "available": ["qwen3:8b"],
            "generation_available": ["qwen3:32b"],
            "cloud": {"anthropic": ["claude-haiku-4-5-20251001"]},
        }
        names = _installed_and_cloud_models(state)
        assert "qwen3:32b" in names
        assert "claude-haiku-4-5-20251001" in names

    def test_sweep_reads_the_api_not_the_local_cli(self):
        """tools/eval_local_models.py gated on `ollama list`, so a remote
        candidate was skipped even after pin_model accepted it."""
        source = _read_source("tools", "eval_local_models.py")
        assert "ollama\", \"list\"" not in source
        assert "subprocess" not in source
        assert "read_model_state()" in source
