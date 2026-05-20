from pathlib import Path

from src.safety.constitution_loader import ConstitutionLoader

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_CONSTITUTION = REPO_ROOT / "config" / "constitution.yaml"


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


# ---------------------------------------------------------------------------
# Real constitution file smoke tests — catch accidental corruption of
# config/constitution.yaml at load time and lock in v0.8 principle structure.
# ---------------------------------------------------------------------------


def test_real_constitution_loads_cleanly() -> None:
    loader = ConstitutionLoader(config_path=REAL_CONSTITUTION)
    constitution = loader.load()

    assert constitution.version == "v0.8"
    principle_ids = {p.id for p in constitution.principles}
    # Lock in the expected principle set so accidental deletions fail loudly.
    expected = {
        "non_harm",
        "truthfulness",
        "usefulness_over_compliance",
        "proportional_safety",
        "user_agency_and_respect",
        "fairness_dignity_non_distortion",
        "relational_honesty",
        "flourishing_over_preference",
        "system_integrity",
    }
    assert expected.issubset(principle_ids)


def test_flourishing_over_preference_v0_2_structure() -> None:
    """Lock in the v0.2 rewrite of flourishing_over_preference — the
    four-condition fire gate, default-to-silence clause, stated-values-
    only constraint, non_harm preemption, and relational_honesty
    delegation must all survive future edits."""
    loader = ConstitutionLoader(config_path=REAL_CONSTITUTION)
    constitution = loader.load()

    principle = next(
        p for p in constitution.principles if p.id == "flourishing_over_preference"
    )

    joined_rules = " ".join(principle.rules).lower()

    # Four-condition fire gate
    assert "all four conditions are met" in joined_rules
    assert "stated a value" in joined_rules
    assert "conflict is clear" in joined_rules
    assert "already named" in joined_rules
    assert "agency fully intact" in joined_rules

    # Default-to-silence clause
    assert "default to silence" in joined_rules
    assert "false positive" in joined_rules

    # Stated-values-only constraint (must not fire on inferred values)
    assert "infers" in joined_rules
    assert "actually stated" in joined_rules

    # non_harm preemption preserved
    assert "non_harm" in joined_rules
    assert "self-harm" in joined_rules or "crisis" in joined_rules

    # relational_honesty delegation preserved
    assert "relational_honesty" in joined_rules
    assert "behavioral sequence" in joined_rules

    # Behavior block trimmed to the three short lines — the verbose
    # "rarely and quietly" / "infinitely accommodating surface" /
    # RESOLVED amplification gate note from v0.1 must be gone.
    joined_behavior = " ".join(principle.behavior).lower()
    assert "rarely and quietly" not in joined_behavior
    assert "infinitely accommodating" not in joined_behavior
    assert "resolved:" not in joined_behavior
    assert "after speaking, help" in joined_behavior