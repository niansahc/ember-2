"""
Tests for LodestoneLoader — lodestone seed layer (ADR-017).
"""

from pathlib import Path

import pytest

from src.safety.lodestone_loader import LodestoneLoader, LodestoneSeed, SeedValue


VALID_SEED_YAML = """
seed_values:
  - name: honesty_over_comfort
    value: "when accuracy and ease conflict, accuracy wins"
    taxonomy_category: character

  - name: growth_over_stasis
    value: "orient toward what moves things forward"
    taxonomy_category: directional
""".strip()


def _write_seed_file(tmp_path: Path, content: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    seed_file = config_dir / "lodestone.yaml"
    seed_file.write_text(content, encoding="utf-8")
    return seed_file


class TestLodestoneLoaderLoad:
    def test_loads_valid_seed(self, tmp_path):
        path = _write_seed_file(tmp_path, VALID_SEED_YAML)
        loader = LodestoneLoader(config_path=path)
        seed = loader.load()
        assert isinstance(seed, LodestoneSeed)
        assert len(seed.values) == 2

    def test_seed_value_fields(self, tmp_path):
        path = _write_seed_file(tmp_path, VALID_SEED_YAML)
        loader = LodestoneLoader(config_path=path)
        seed = loader.load()
        first = seed.values[0]
        assert first.name == "honesty_over_comfort"
        assert first.value == "when accuracy and ease conflict, accuracy wins"
        assert first.taxonomy_category == "character"

    def test_file_not_found_raises(self, tmp_path):
        loader = LodestoneLoader(config_path=tmp_path / "missing.yaml")
        with pytest.raises(FileNotFoundError):
            loader.load()

    def test_non_mapping_raises(self, tmp_path):
        path = _write_seed_file(tmp_path, "- just a list")
        loader = LodestoneLoader(config_path=path)
        with pytest.raises(ValueError, match="top-level mapping"):
            loader.load()

    def test_empty_seed_values_raises(self, tmp_path):
        path = _write_seed_file(tmp_path, "seed_values: []")
        loader = LodestoneLoader(config_path=path)
        with pytest.raises(ValueError, match="non-empty"):
            loader.load()

    def test_missing_seed_values_key_raises(self, tmp_path):
        path = _write_seed_file(tmp_path, "other_key: true")
        loader = LodestoneLoader(config_path=path)
        with pytest.raises(ValueError, match="non-empty"):
            loader.load()

    def test_entry_missing_name_raises(self, tmp_path):
        content = """
seed_values:
  - value: "test"
    taxonomy_category: character
""".strip()
        path = _write_seed_file(tmp_path, content)
        loader = LodestoneLoader(config_path=path)
        with pytest.raises(ValueError, match="name"):
            loader.load()

    def test_entry_missing_value_raises(self, tmp_path):
        content = """
seed_values:
  - name: test
    taxonomy_category: character
""".strip()
        path = _write_seed_file(tmp_path, content)
        loader = LodestoneLoader(config_path=path)
        with pytest.raises(ValueError, match="value"):
            loader.load()

    def test_entry_missing_taxonomy_raises(self, tmp_path):
        content = """
seed_values:
  - name: test
    value: "test value"
""".strip()
        path = _write_seed_file(tmp_path, content)
        loader = LodestoneLoader(config_path=path)
        with pytest.raises(ValueError, match="taxonomy_category"):
            loader.load()

    def test_entry_not_mapping_raises(self, tmp_path):
        content = """
seed_values:
  - "just a string"
""".strip()
        path = _write_seed_file(tmp_path, content)
        loader = LodestoneLoader(config_path=path)
        with pytest.raises(ValueError, match="mapping"):
            loader.load()


class TestLodestoneLoaderCache:
    def test_get_seed_caches(self, tmp_path):
        path = _write_seed_file(tmp_path, VALID_SEED_YAML)
        loader = LodestoneLoader(config_path=path)
        seed1 = loader.get_seed()
        seed2 = loader.get_seed()
        assert seed1 is seed2


class TestLodestonePromptText:
    def test_to_prompt_text_format(self, tmp_path):
        path = _write_seed_file(tmp_path, VALID_SEED_YAML)
        loader = LodestoneLoader(config_path=path)
        text = loader.to_prompt_text()
        assert text.startswith("Ember's orientation (lodestone):")
        assert "when accuracy and ease conflict, accuracy wins" in text
        assert "orient toward what moves things forward" in text

    def test_to_prompt_text_no_names(self, tmp_path):
        """Seed prompt text should contain values, not internal names."""
        path = _write_seed_file(tmp_path, VALID_SEED_YAML)
        loader = LodestoneLoader(config_path=path)
        text = loader.to_prompt_text()
        assert "honesty_over_comfort" not in text
        assert "growth_over_stasis" not in text

    def test_to_prompt_text_line_count(self, tmp_path):
        path = _write_seed_file(tmp_path, VALID_SEED_YAML)
        loader = LodestoneLoader(config_path=path)
        text = loader.to_prompt_text()
        lines = text.strip().split("\n")
        # Header + 2 values
        assert len(lines) == 3


class TestLodestoneDefaultPath:
    def test_default_path_points_to_config(self):
        loader = LodestoneLoader()
        assert loader.config_path.name == "lodestone.yaml"
        assert "config" in str(loader.config_path)


class TestLodestoneProductionFile:
    def test_production_lodestone_loads(self):
        """The actual config/lodestone.yaml must load without errors."""
        loader = LodestoneLoader()
        seed = loader.load()
        assert len(seed.values) == 5

    def test_production_lodestone_prompt_text(self):
        """The actual config/lodestone.yaml must render prompt text."""
        loader = LodestoneLoader()
        text = loader.to_prompt_text()
        assert "Ember's orientation" in text
        assert len(text.strip().split("\n")) == 6  # header + 5 values
