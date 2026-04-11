"""
Tests for IdentityRulesLoader — identity defense rules (ADR-016 amendment).
"""

from pathlib import Path

import pytest

from src.safety.identity_rules_loader import IdentityRulesLoader


VALID_RULES_YAML = """
version: "v0.1"

rules:
  - id: ai_question
    trigger: "asked if you are an AI"
    rule: "Acknowledge technical reality without abandoning identity."
  - id: memory_gap
    trigger: "asked about something not in vault"
    rule: "Say directly: I don't have that in my memory."

metadata:
  created: "2026-04-04"
""".strip()


def _write_rules_file(tmp_path: Path, content: str) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    rules_file = config_dir / "identity_rules.yaml"
    rules_file.write_text(content, encoding="utf-8")
    return rules_file


def test_loads_valid_identity_rules(tmp_path: Path) -> None:
    rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
    loader = IdentityRulesLoader(config_path=rules_file)
    rules = loader.load()

    assert rules.version == "v0.1"
    assert len(rules.rules) == 2
    assert rules.rules[0].id == "ai_question"
    assert rules.rules[1].id == "memory_gap"


def test_to_prompt_text_renders_all_rules(tmp_path: Path) -> None:
    rules_file = _write_rules_file(tmp_path, VALID_RULES_YAML)
    loader = IdentityRulesLoader(config_path=rules_file)
    loader.load()

    text = loader.to_prompt_text()

    assert text.startswith("Identity rules:")
    assert "When asked if you are an AI: Acknowledge technical reality" in text
    assert "When asked about something not in vault: Say directly" in text


def test_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    loader = IdentityRulesLoader(config_path=tmp_path / "nonexistent.yaml")
    with pytest.raises(FileNotFoundError):
        loader.load()


def test_missing_version_raises_value_error(tmp_path: Path) -> None:
    content = """
rules:
  - id: test
    trigger: "test trigger"
    rule: "test rule"
""".strip()
    rules_file = _write_rules_file(tmp_path, content)
    loader = IdentityRulesLoader(config_path=rules_file)
    with pytest.raises(ValueError, match="version"):
        loader.load()


def test_missing_rules_list_raises_value_error(tmp_path: Path) -> None:
    content = 'version: "v0.1"'
    rules_file = _write_rules_file(tmp_path, content)
    loader = IdentityRulesLoader(config_path=rules_file)
    with pytest.raises(ValueError, match="rules"):
        loader.load()


def test_missing_rule_id_raises_value_error(tmp_path: Path) -> None:
    content = """
version: "v0.1"
rules:
  - trigger: "test"
    rule: "test"
""".strip()
    rules_file = _write_rules_file(tmp_path, content)
    loader = IdentityRulesLoader(config_path=rules_file)
    with pytest.raises(ValueError, match="id"):
        loader.load()


def test_missing_trigger_raises_value_error(tmp_path: Path) -> None:
    content = """
version: "v0.1"
rules:
  - id: test
    rule: "test"
""".strip()
    rules_file = _write_rules_file(tmp_path, content)
    loader = IdentityRulesLoader(config_path=rules_file)
    with pytest.raises(ValueError, match="trigger"):
        loader.load()


def test_missing_rule_raises_value_error(tmp_path: Path) -> None:
    content = """
version: "v0.1"
rules:
  - id: test
    trigger: "test"
""".strip()
    rules_file = _write_rules_file(tmp_path, content)
    loader = IdentityRulesLoader(config_path=rules_file)
    with pytest.raises(ValueError, match="rule"):
        loader.load()


def test_loads_real_identity_rules_file() -> None:
    """Verify the actual config/identity_rules.yaml loads correctly."""
    loader = IdentityRulesLoader()
    rules = loader.load()
    assert rules.version == "v0.1"
    # 2 original situational rules (ai_question, closing_questions) +
    # 6 stance rules (preference_expression, greeting_and_state,
    # emotional_presence, identity_under_pressure, refusal_voice,
    # response_length) + 1 override defense rule (override_attempt).
    # Two original rules (preference_question, personality_challenge)
    # were removed as redundant — superseded by the more detailed
    # preference_expression and identity_under_pressure rules.
    assert len(rules.rules) == 9
