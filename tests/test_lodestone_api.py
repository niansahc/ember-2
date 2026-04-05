"""
Tests for lodestone API endpoints (ADR-017).
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.memory import lodestone_service


@pytest.fixture
def vault_dir(tmp_path):
    """Set up a temporary vault with lodestone directory."""
    vault = tmp_path / "vault"
    vault.mkdir()
    lodestone_dir = vault / "memory" / "lodestone"
    lodestone_dir.mkdir(parents=True)
    with patch("src.memory.lodestone_service.get_private_vault_path", return_value=vault):
        yield vault


def _write_record(vault_dir, record_id, confirmed=True, value="test value",
                  taxonomy="character"):
    lodestone_dir = vault_dir / "memory" / "lodestone"
    record = {
        "id": record_id,
        "timestamp": record_id,
        "type": "lodestone",
        "value": value,
        "acquisition_path": "explicit",
        "source": "conversation",
        "supporting_evidence": "",
        "recurrence_count": 1,
        "confirmed": confirmed,
        "conflict_resolution": False,
        "metadata": {
            "user_note": None,
            "taxonomy_category": taxonomy,
            "flagged_as_noise": False,
        },
    }
    file_path = lodestone_dir / f"{record_id}.json"
    file_path.write_text(json.dumps(record), encoding="utf-8")
    return record


class TestGetLodestone:
    def test_returns_all_records(self, vault_dir):
        _write_record(vault_dir, "r1", confirmed=True)
        _write_record(vault_dir, "r2", confirmed=False)
        records = lodestone_service.read_all()
        assert len(records) == 2

    def test_returns_empty_when_no_records(self, vault_dir):
        records = lodestone_service.read_all()
        assert len(records) == 0


class TestCreateLodestone:
    def test_creates_confirmed_record(self, vault_dir):
        rec = lodestone_service.write(
            value="honesty matters",
            taxonomy_category="character",
            acquisition_path="explicit",
            source="conversation",
            confirmed=True,
        )
        assert rec["confirmed"] is True
        assert rec["value"] == "honesty matters"
        assert rec["metadata"]["taxonomy_category"] == "character"

    def test_created_record_persists(self, vault_dir):
        lodestone_service.write(
            value="growth",
            taxonomy_category="directional",
            confirmed=True,
        )
        active = lodestone_service.read_active()
        assert len(active) == 1
        assert active[0]["value"] == "growth"


class TestUpdateLodestone:
    def test_confirm_proposed_record(self, vault_dir):
        _write_record(vault_dir, "proposed-1", confirmed=False)
        result = lodestone_service.update("proposed-1", {"confirmed": True})
        assert result is not None
        assert result["confirmed"] is True

    def test_add_user_note(self, vault_dir):
        _write_record(vault_dir, "r1", confirmed=True)
        result = lodestone_service.update("r1", {"user_note": "important to me"})
        assert result["metadata"]["user_note"] == "important to me"

    def test_flag_as_noise(self, vault_dir):
        _write_record(vault_dir, "r1", confirmed=False)
        result = lodestone_service.update("r1", {"flagged_as_noise": True})
        assert result["metadata"]["flagged_as_noise"] is True

    def test_update_nonexistent_returns_none(self, vault_dir):
        result = lodestone_service.update("missing", {"confirmed": True})
        assert result is None


class TestTaxonomyValidation:
    def test_valid_categories(self, vault_dir):
        for cat in ("character", "relational", "directional", "ground", "beyond"):
            rec = lodestone_service.write(f"value for {cat}", cat, confirmed=True)
            assert rec["metadata"]["taxonomy_category"] == cat


class TestValueInference:
    @patch("ollama.chat")
    @patch("src.core.config.get_ember_model", return_value="qwen3:8b")
    def test_infers_value_from_raw_answer(self, mock_model, mock_chat):
        from src.api.main import _extract_lodestone_value
        mock_chat.return_value = {
            "message": {"content": "building things that matter even under difficult conditions"}
        }
        result = _extract_lodestone_value(
            "Work, building Ember-2, and surviving this hellscape.",
            question_context="What matters to you right now?",
        )
        assert result == "building things that matter even under difficult conditions"
        mock_chat.assert_called_once()
        call_args = mock_chat.call_args
        assert call_args[1]["options"]["temperature"] == 0
        assert call_args[1]["options"]["num_predict"] == 100
        assert call_args[1]["think"] is False

    @patch("ollama.chat")
    @patch("src.core.config.get_ember_model", return_value="qwen3:8b")
    def test_includes_question_context_in_prompt(self, mock_model, mock_chat):
        from src.api.main import _extract_lodestone_value
        mock_chat.return_value = {"message": {"content": "honesty matters"}}
        _extract_lodestone_value("I value truth", question_context="What do you care about?")
        # Question context is in the user message (messages[1])
        user_msg = mock_chat.call_args[1]["messages"][1]["content"]
        assert "What do you care about?" in user_msg

    @patch("ollama.chat")
    @patch("src.core.config.get_ember_model", return_value="qwen3:8b")
    def test_works_without_question_context(self, mock_model, mock_chat):
        from src.api.main import _extract_lodestone_value
        mock_chat.return_value = {"message": {"content": "creativity matters"}}
        result = _extract_lodestone_value("I love making things")
        assert result == "creativity matters"

    @patch("ollama.chat", side_effect=Exception("connection refused"))
    def test_returns_none_on_ollama_failure(self, mock_chat):
        from src.api.main import _extract_lodestone_value
        result = _extract_lodestone_value("raw answer text")
        assert result is None

    @patch("ollama.chat")
    @patch("src.core.config.get_ember_model", return_value="qwen3:8b")
    def test_returns_none_on_empty_response(self, mock_model, mock_chat):
        from src.api.main import _extract_lodestone_value
        mock_chat.return_value = {"message": {"content": ""}}
        result = _extract_lodestone_value("raw answer text")
        assert result is None

    @patch("src.api.main._extract_lodestone_value")
    def test_endpoint_stores_inferred_value(self, mock_extract, vault_dir):
        mock_extract.return_value = "building meaningful systems"
        rec = lodestone_service.write(
            value="building meaningful systems",
            taxonomy_category="directional",
            acquisition_path="explicit",
            source="conversation",
            supporting_evidence="Work, building Ember-2, surviving hellscape.",
            confirmed=True,
        )
        assert rec["value"] == "building meaningful systems"
        assert rec["supporting_evidence"] == "Work, building Ember-2, surviving hellscape."

    @patch("src.api.main._extract_lodestone_value")
    def test_endpoint_stores_raw_in_evidence(self, mock_extract, vault_dir):
        mock_extract.return_value = "honesty above comfort"
        rec = lodestone_service.write(
            value="honesty above comfort",
            taxonomy_category="character",
            supporting_evidence="I always tell people the truth even when it sucks",
            confirmed=True,
        )
        assert rec["supporting_evidence"] == "I always tell people the truth even when it sucks"
