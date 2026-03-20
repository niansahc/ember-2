from pathlib import Path

from src.safety.constitution_loader import ConstitutionLoader


def test_constitution_loader_reads_valid_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    constitution_file = config_dir / "constitution.yaml"
    constitution_file.write_text(
        """
version: "v-test"

principles:
  - id: non_harm
    title: "Non-Harm"
    intent: "Avoid enabling harm."
    rules:
      - "Do not provide harmful instructions."
    behavior:
      - "Prefer clarity over restriction."

execution:
  outcomes:
    - "allow"
    - "revise"
    - "refuse_redirect"
  logging:
    - "triggered"
    - "outcome"
    - "rules"

metadata:
  default_mode: "hybrid"
  review_strategy: "triggered_post_draft"
  notes:
    - "test constitution"
""".strip(),
        encoding="utf-8",
    )

    loader = ConstitutionLoader(config_path=constitution_file)
    constitution = loader.load()

    assert constitution.version == "v-test"
    assert len(constitution.principles) == 1
    assert constitution.principles[0].id == "non_harm"
    assert constitution.execution.outcomes == ["allow", "revise", "refuse_redirect"]
    assert constitution.metadata.default_mode == "hybrid"


def test_constitution_loader_to_prompt_text_contains_principle(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    constitution_file = config_dir / "constitution.yaml"
    constitution_file.write_text(
        """
version: "v-test"

principles:
  - id: truthfulness
    title: "Truthfulness"
    intent: "Be accurate."
    rules:
      - "Do not fabricate facts."
    behavior:
      - "State uncertainty clearly."

execution:
  outcomes:
    - "allow"
  logging:
    - "triggered"

metadata:
  default_mode: "hybrid"
  review_strategy: "triggered_post_draft"
  notes:
    - "test constitution"
""".strip(),
        encoding="utf-8",
    )

    loader = ConstitutionLoader(config_path=constitution_file)
    constitution = loader.load()
    prompt_text = constitution.to_prompt_text()

    assert "Constitution Version: v-test" in prompt_text
    assert "[truthfulness] Truthfulness" in prompt_text
    assert "Do not fabricate facts." in prompt_text