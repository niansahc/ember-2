"""
Tests for NatureLoader — Ember's nature layer (ADR-016).
"""

import logging
from pathlib import Path

import pytest

from src.safety.nature_loader import NatureLoader


VALID_NATURE_YAML = """
version: "v0.1"

nature:
  - id: sincerity
    name: Sincerity
    description: "genuine interest and care; does not perform either"
  - id: directness
    name: Directness
    description: "says what she thinks; does not hedge to manage comfort"
  - id: economy
    name: Economy
    description: "uses only the words the thought requires"

metadata:
  created: "2026-04-03"
  authored_by: "test"
""".strip()


def _write_nature_file(tmp_path: Path, content: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    nature_file = config_dir / "nature.yaml"
    nature_file.write_text(content, encoding="utf-8")
    return nature_file


# ── Load and parse ──────────────────────────────────────────────────────


def test_loads_valid_nature_yaml(tmp_path: Path) -> None:
    nature_file = _write_nature_file(tmp_path, VALID_NATURE_YAML)
    loader = NatureLoader(config_path=nature_file)
    nature = loader.load()

    assert nature.version == "v0.1"
    assert len(nature.entries) == 3
    assert nature.entries[0].id == "sincerity"
    assert nature.entries[0].name == "Sincerity"
    assert nature.entries[1].id == "directness"
    assert nature.entries[2].id == "economy"


def test_to_prompt_text_renders_all_entries(tmp_path: Path) -> None:
    nature_file = _write_nature_file(tmp_path, VALID_NATURE_YAML)
    loader = NatureLoader(config_path=nature_file)
    loader.load()

    text = loader.to_prompt_text()

    assert text.startswith("Ember's nature:")
    assert "- Sincerity: genuine interest and care; does not perform either" in text
    assert "- Directness: says what she thinks; does not hedge to manage comfort" in text
    assert "- Economy: uses only the words the thought requires" in text


def test_to_prompt_text_format_is_dash_prefixed_lines(tmp_path: Path) -> None:
    nature_file = _write_nature_file(tmp_path, VALID_NATURE_YAML)
    loader = NatureLoader(config_path=nature_file)
    loader.load()

    lines = loader.to_prompt_text().split("\n")
    assert lines[0] == "Ember's nature:"
    for line in lines[1:]:
        assert line.startswith("- "), f"Expected dash-prefixed line, got: {line}"


# ── Error handling ──────────────────────────────────────────────────────


def test_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    loader = NatureLoader(config_path=tmp_path / "nonexistent.yaml")
    with pytest.raises(FileNotFoundError):
        loader.load()


def test_missing_version_raises_value_error(tmp_path: Path) -> None:
    content = """
nature:
  - id: test
    name: Test
    description: "test entry"
""".strip()
    nature_file = _write_nature_file(tmp_path, content)
    loader = NatureLoader(config_path=nature_file)
    with pytest.raises(ValueError, match="version"):
        loader.load()


def test_missing_nature_list_raises_value_error(tmp_path: Path) -> None:
    content = 'version: "v0.1"'
    nature_file = _write_nature_file(tmp_path, content)
    loader = NatureLoader(config_path=nature_file)
    with pytest.raises(ValueError, match="nature"):
        loader.load()


def test_empty_nature_list_raises_value_error(tmp_path: Path) -> None:
    content = """
version: "v0.1"
nature: []
""".strip()
    nature_file = _write_nature_file(tmp_path, content)
    loader = NatureLoader(config_path=nature_file)
    with pytest.raises(ValueError, match="nature"):
        loader.load()


def test_missing_entry_id_raises_value_error(tmp_path: Path) -> None:
    content = """
version: "v0.1"
nature:
  - name: Test
    description: "test"
""".strip()
    nature_file = _write_nature_file(tmp_path, content)
    loader = NatureLoader(config_path=nature_file)
    with pytest.raises(ValueError, match="id"):
        loader.load()


def test_missing_entry_name_raises_value_error(tmp_path: Path) -> None:
    content = """
version: "v0.1"
nature:
  - id: test
    description: "test"
""".strip()
    nature_file = _write_nature_file(tmp_path, content)
    loader = NatureLoader(config_path=nature_file)
    with pytest.raises(ValueError, match="name"):
        loader.load()


def test_missing_entry_description_raises_value_error(tmp_path: Path) -> None:
    content = """
version: "v0.1"
nature:
  - id: test
    name: Test
""".strip()
    nature_file = _write_nature_file(tmp_path, content)
    loader = NatureLoader(config_path=nature_file)
    with pytest.raises(ValueError, match="description"):
        loader.load()


# ── Version change detection ────────────────────────────────────────────


def test_version_change_logs_warning(tmp_path: Path, caplog) -> None:
    nature_file = _write_nature_file(tmp_path, VALID_NATURE_YAML)

    # Create a fake version file with an old version
    system_dir = tmp_path / "system"
    system_dir.mkdir(parents=True, exist_ok=True)
    version_file = system_dir / "nature_version.txt"
    version_file.write_text("v0.0", encoding="utf-8")

    loader = NatureLoader(config_path=nature_file)
    # Override version file path to use tmp_path
    loader._version_file_path = lambda: version_file

    with caplog.at_level(logging.WARNING, logger="ember.nature"):
        loader.load()

    assert "Version changed" in caplog.text
    assert "v0.0" in caplog.text
    assert "v0.1" in caplog.text


def test_same_version_no_warning(tmp_path: Path, caplog) -> None:
    nature_file = _write_nature_file(tmp_path, VALID_NATURE_YAML)

    system_dir = tmp_path / "system"
    system_dir.mkdir(parents=True, exist_ok=True)
    version_file = system_dir / "nature_version.txt"
    version_file.write_text("v0.1", encoding="utf-8")

    loader = NatureLoader(config_path=nature_file)
    loader._version_file_path = lambda: version_file

    with caplog.at_level(logging.WARNING, logger="ember.nature"):
        loader.load()

    assert "Version changed" not in caplog.text


# ── Singleton / caching ─────────────────────────────────────────────────


def test_get_nature_caches_after_first_load(tmp_path: Path) -> None:
    nature_file = _write_nature_file(tmp_path, VALID_NATURE_YAML)
    loader = NatureLoader(config_path=nature_file)

    nature1 = loader.get_nature()
    nature2 = loader.get_nature()
    assert nature1 is nature2


# ── Prompt builder integration ──────────────────────────────────────────


def test_nature_block_appears_before_state_in_prompt(tmp_path: Path) -> None:
    """Nature block must appear before CURRENT STATE in the assembled prompt."""
    nature_file = _write_nature_file(tmp_path, VALID_NATURE_YAML)

    from src.llm.prompt_builder import PromptBuilder
    from src.context.models import ContextPacket

    builder = PromptBuilder()
    # Inject a test nature loader
    builder._nature_loader = NatureLoader(config_path=nature_file)
    builder._nature_loader.load()

    packet = ContextPacket(
        user_message="hello",
        memory_items=[],
        reflection_items=[],
        state_items=[],
        task_items=[],
        web_items=[],
        image_data=[],
    )

    prompt = builder.build_prompt(packet)

    nature_pos = prompt.find("Ember's nature:")
    state_pos = prompt.find("CURRENT STATE:")

    assert nature_pos != -1, "Nature block not found in prompt"
    assert state_pos != -1, "State section not found in prompt"
    assert nature_pos < state_pos, "Nature block must appear before state section"
