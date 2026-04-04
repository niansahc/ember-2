"""
Tests for embedding model filtering from model selector.
"""

from unittest.mock import patch


def test_embedding_models_filtered_from_list():
    """nomic-embed-text should not appear in available models."""
    fake_models = {
        "models": [
            {"model": "qwen3:8b"},
            {"model": "nomic-embed-text:latest"},
            {"model": "mistral:7b"},
            {"model": "all-minilm-embedding:latest"},
        ]
    }

    with patch("src.api.main.ollama") as mock_ollama:
        mock_ollama.list.return_value = fake_models

        from src.api.main import get_model_endpoint
        result = get_model_endpoint()

        assert "qwen3:8b" in result["available"]
        assert "mistral:7b" in result["available"]
        assert "nomic-embed-text:latest" not in result["available"]
        assert "all-minilm-embedding:latest" not in result["available"]
