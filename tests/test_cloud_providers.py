"""
tests/test_cloud_providers.py

Tests for cloud model provider dispatch and key management.
Uses mocks — no real API calls are made.
"""

import pytest
from unittest.mock import patch, MagicMock

from src.llm.adapter import LLMAdapter
from src.core.config import get_cloud_models, CLOUD_MODELS


class TestModelDispatch:
    """Model name prefix determines which provider is used."""

    def test_claude_model_is_cloud(self):
        adapter = LLMAdapter(model="claude-sonnet-4-5-20250929")
        assert adapter._is_cloud_model("claude-sonnet-4-5-20250929")

    def test_claude_haiku_is_cloud(self):
        adapter = LLMAdapter(model="claude-haiku-4-5-20251001")
        assert adapter._is_cloud_model("claude-haiku-4-5-20251001")

    def test_qwen_is_not_cloud(self):
        adapter = LLMAdapter(model="qwen3:8b")
        assert not adapter._is_cloud_model("qwen3:8b")

    def test_llama_is_not_cloud(self):
        adapter = LLMAdapter(model="llama3.1:8b")
        assert not adapter._is_cloud_model("llama3.1:8b")

    def test_chat_dispatches_to_anthropic_for_claude(self):
        adapter = LLMAdapter(model="claude-sonnet-4-5-20250929")
        with patch.object(adapter, '_chat_anthropic', return_value="Hello from Claude") as mock:
            result = adapter._chat("system", "hello")
            mock.assert_called_once()
            assert result == "Hello from Claude"

    def test_chat_dispatches_to_ollama_for_local(self):
        adapter = LLMAdapter(model="qwen3:8b")
        with patch.object(adapter, '_chat_ollama', return_value="Hello from Qwen") as mock:
            result = adapter._chat("system", "hello")
            mock.assert_called_once()
            assert result == "Hello from Qwen"

    def test_stream_dispatches_to_anthropic_for_claude(self):
        adapter = LLMAdapter(model="claude-sonnet-4-5-20250929")
        with patch.object(adapter, '_chat_anthropic_stream', return_value=iter(["Hello"])) as mock:
            chunks = list(adapter._chat_stream("system", "hello"))
            mock.assert_called_once()
            assert chunks == ["Hello"]

    def test_stream_dispatches_to_ollama_for_local(self):
        adapter = LLMAdapter(model="qwen3:8b")
        with patch.object(adapter, '_chat_ollama_stream', return_value=iter(["Hi"])) as mock:
            chunks = list(adapter._chat_stream("system", "hello"))
            mock.assert_called_once()
            assert chunks == ["Hi"]


class TestProviderApiKey:
    """Provider API key storage and retrieval."""

    def test_get_key_returns_none_when_not_configured(self):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        with patch.dict("sys.modules", {"keyring": mock_kr}), \
             patch.dict("os.environ", {}, clear=True):
            result = LLMAdapter._get_provider_api_key("anthropic")
            assert result is None

    def test_get_key_returns_key_when_configured(self):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = "sk-ant-test123"
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            result = LLMAdapter._get_provider_api_key("anthropic")
            assert result == "sk-ant-test123"
            mock_kr.get_password.assert_called_with("ember-2-anthropic", "api_key")

    def test_get_key_never_raises(self):
        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = Exception("keyring broken")
        with patch.dict("sys.modules", {"keyring": mock_kr}), \
             patch.dict("os.environ", {}, clear=True):
            result = LLMAdapter._get_provider_api_key("anthropic")
            assert result is None

    def test_get_key_falls_back_to_env_var(self):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        with patch.dict("sys.modules", {"keyring": mock_kr}), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-env-test"}, clear=True):
            result = LLMAdapter._get_provider_api_key("anthropic")
            assert result == "sk-env-test"

    def test_anthropic_chat_raises_without_key(self):
        adapter = LLMAdapter(model="claude-sonnet-4-5-20250929")
        with patch.object(LLMAdapter, '_get_provider_api_key', return_value=None):
            with pytest.raises(ValueError, match="No Anthropic API key"):
                adapter._chat_anthropic("system", "hello")


class TestCloudModelsConfig:
    """Cloud model catalog configuration."""

    def test_anthropic_models_listed(self):
        models = get_cloud_models()
        assert "anthropic" in models
        assert "claude-sonnet-4-5-20250929" in models["anthropic"]

    def test_get_cloud_models_returns_dict(self):
        result = get_cloud_models()
        assert isinstance(result, dict)

    def test_cloud_models_matches_constant(self):
        assert get_cloud_models() is CLOUD_MODELS

    def test_openai_models_listed(self):
        models = get_cloud_models()
        assert "openai" in models
        assert "gpt-4o-mini" in models["openai"]
        assert "gpt-4o" in models["openai"]


class TestOpenAIDispatch:
    """OpenAI model routing."""

    def test_gpt_model_is_cloud(self):
        adapter = LLMAdapter(model="gpt-4o-mini")
        assert adapter._is_cloud_model("gpt-4o-mini")

    def test_gpt4o_is_cloud(self):
        adapter = LLMAdapter(model="gpt-4o")
        assert adapter._is_cloud_model("gpt-4o")

    def test_chat_dispatches_to_openai_for_gpt(self):
        adapter = LLMAdapter(model="gpt-4o-mini")
        with patch.object(adapter, '_chat_openai', return_value="Hello from GPT") as mock:
            result = adapter._chat("system", "hello")
            mock.assert_called_once()
            assert result == "Hello from GPT"

    def test_stream_dispatches_to_openai_for_gpt(self):
        adapter = LLMAdapter(model="gpt-4o-mini")
        with patch.object(adapter, '_chat_openai_stream', return_value=iter(["Hi"])) as mock:
            chunks = list(adapter._chat_stream("system", "hello"))
            mock.assert_called_once()
            assert chunks == ["Hi"]

    def test_openai_chat_raises_without_key(self):
        adapter = LLMAdapter(model="gpt-4o-mini")
        with patch.object(LLMAdapter, '_get_provider_api_key', return_value=None):
            with pytest.raises(ValueError, match="No OpenAI API key"):
                adapter._chat_openai("system", "hello")

    def test_openai_key_falls_back_to_env_var(self):
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = None
        with patch.dict("sys.modules", {"keyring": mock_kr}), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "sk-openai-test"}, clear=True):
            result = LLMAdapter._get_provider_api_key("openai")
            assert result == "sk-openai-test"
