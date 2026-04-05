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
