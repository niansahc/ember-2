"""
src/safety/lodestone_loader.py

Loads the lodestone seed layer from config/lodestone.yaml.

The seed layer defines Ember's default orientation values -- what Ember
is oriented toward on the user's behalf before any living layer values
accumulate. Injected into the system prompt for primacy.

See ADR-017 (Lodestone Layer) and TDD §48.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("ember.lodestone")


@dataclass(frozen=True)
class SeedValue:
    name: str
    value: str
    taxonomy_category: str


@dataclass(frozen=True)
class LodestoneSeed:
    values: list[SeedValue]

    def to_prompt_text(self) -> str:
        """Render the seed layer for system prompt injection."""
        lines = ["Ember's orientation (lodestone):"]
        for sv in self.values:
            lines.append(f"- {sv.value}")
        return "\n".join(lines)


class LodestoneLoader:
    """Loads and caches the lodestone seed layer from config/lodestone.yaml."""

    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or self._default_config_path()
        self._seed: LodestoneSeed | None = None

    def load(self) -> LodestoneSeed:
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Lodestone seed file not found: {self.config_path}"
            )

        with self.config_path.open("r", encoding="utf-8") as file:
            raw_data = yaml.safe_load(file)

        if not isinstance(raw_data, dict):
            raise ValueError("Lodestone seed file must contain a top-level mapping.")

        seed = self._parse_seed(raw_data)
        self._seed = seed
        return seed

    def get_seed(self) -> LodestoneSeed:
        """Return cached seed, loading if needed."""
        if self._seed is None:
            self._seed = self.load()
        return self._seed

    def to_prompt_text(self) -> str:
        """Render the seed layer for system prompt injection."""
        return self.get_seed().to_prompt_text()

    @staticmethod
    def _default_config_path() -> Path:
        base_dir = Path(__file__).resolve().parents[2]
        return base_dir / "config" / "lodestone.yaml"

    def _parse_seed(self, raw_data: dict[str, Any]) -> LodestoneSeed:
        raw_values = raw_data.get("seed_values")
        if not isinstance(raw_values, list) or not raw_values:
            raise ValueError(
                "Lodestone seed file must include a non-empty 'seed_values' list."
            )

        values = [self._parse_value(item) for item in raw_values]
        return LodestoneSeed(values=values)

    def _parse_value(self, raw_entry: Any) -> SeedValue:
        if not isinstance(raw_entry, dict):
            raise ValueError("Each seed value must be a mapping.")

        name = raw_entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each seed value must have a non-empty 'name' string.")

        value = raw_entry.get("value")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Each seed value must have a non-empty 'value' string.")

        taxonomy_category = raw_entry.get("taxonomy_category")
        if not isinstance(taxonomy_category, str) or not taxonomy_category.strip():
            raise ValueError(
                "Each seed value must have a non-empty 'taxonomy_category' string."
            )

        return SeedValue(
            name=name.strip(),
            value=value.strip(),
            taxonomy_category=taxonomy_category.strip(),
        )
