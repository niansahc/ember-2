"""
Tests for LodestoneResolver — lodestone living layer retrieval (ADR-017).
"""

from unittest.mock import patch

import pytest

from src.context.lodestone_resolver import (
    TOKEN_BUDGET,
    _cosine_similarity,
    _token_estimate,
    resolve,
    to_prompt_text,
)


def _make_record(value, record_id="r1", confirmed=True, taxonomy="character"):
    return {
        "id": record_id,
        "type": "lodestone",
        "value": value,
        "confirmed": confirmed,
        "metadata": {"taxonomy_category": taxonomy},
    }


# Simple embeddings: use one-hot-like vectors for controlled similarity
EMBEDDINGS = {
    "honesty matters": [1.0, 0.0, 0.0],
    "growth is key": [0.0, 1.0, 0.0],
    "curiosity wins": [0.0, 0.0, 1.0],
    "tell me the truth": [0.9, 0.1, 0.0],  # Similar to honesty
    "how should I grow": [0.1, 0.9, 0.0],  # Similar to growth
    "what are you working on": [0.3, 0.3, 0.4],  # Mixed
}


def _mock_embed(text):
    return EMBEDDINGS.get(text, [0.33, 0.33, 0.33])


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert _cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1, 0, 0], [0, 1, 0]) == pytest.approx(0.0)

    def test_zero_vector(self):
        assert _cosine_similarity([0, 0, 0], [1, 0, 0]) == 0.0


class TestTokenEstimate:
    def test_single_word(self):
        assert _token_estimate("hello") == 1

    def test_multi_word(self):
        assert _token_estimate("when accuracy and ease conflict accuracy wins") == 9


class TestResolve:
    @patch("src.context.lodestone_resolver.read_active")
    @patch("src.context.lodestone_resolver.embed_text", side_effect=_mock_embed)
    def test_returns_most_relevant(self, mock_embed, mock_active):
        mock_active.return_value = [
            _make_record("honesty matters", "r1"),
            _make_record("growth is key", "r2"),
            _make_record("curiosity wins", "r3"),
        ]
        results = resolve("tell me the truth")
        assert len(results) <= 2
        # Honesty should rank highest — closest to "tell me the truth"
        assert results[0]["value"] == "honesty matters"

    @patch("src.context.lodestone_resolver.read_active")
    @patch("src.context.lodestone_resolver.embed_text", side_effect=_mock_embed)
    def test_max_records_limit(self, mock_embed, mock_active):
        mock_active.return_value = [
            _make_record("honesty matters", "r1"),
            _make_record("growth is key", "r2"),
            _make_record("curiosity wins", "r3"),
        ]
        results = resolve("what are you working on", max_records=1)
        assert len(results) == 1

    @patch("src.context.lodestone_resolver.read_active")
    def test_empty_active_returns_empty(self, mock_active):
        mock_active.return_value = []
        assert resolve("anything") == []

    @patch("src.context.lodestone_resolver.read_active")
    @patch("src.context.lodestone_resolver.embed_text", side_effect=Exception("no ollama"))
    def test_embedding_failure_returns_empty(self, mock_embed, mock_active):
        mock_active.return_value = [_make_record("test")]
        assert resolve("anything") == []

    @patch("src.context.lodestone_resolver.read_active")
    @patch("src.context.lodestone_resolver.embed_text", side_effect=_mock_embed)
    def test_token_budget_respected(self, mock_embed, mock_active):
        # Create a record with a very long value that exceeds budget
        long_value = " ".join(["word"] * 200)
        mock_active.return_value = [
            _make_record(long_value, "long"),
            _make_record("short", "short"),
        ]
        results = resolve("anything")
        # Long record should be skipped due to budget
        total_tokens = sum(_token_estimate(r["value"]) for r in results)
        assert total_tokens <= TOKEN_BUDGET


class TestToPromptText:
    def test_renders_values(self):
        records = [
            _make_record("honesty matters"),
            _make_record("growth is key"),
        ]
        text = to_prompt_text(records)
        assert "<lodestone_living>" in text
        assert "honesty matters" in text
        assert "growth is key" in text
        assert "</lodestone_living>" in text

    def test_empty_returns_empty_string(self):
        assert to_prompt_text([]) == ""

    def test_no_internal_names(self):
        records = [_make_record("honesty matters")]
        text = to_prompt_text(records)
        assert "r1" not in text
        assert "character" not in text
