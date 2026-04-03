"""
src/safety/nature_loader.py

Loads Ember's nature document from config/nature.yaml.

The nature layer defines who Ember is -- her dispositions, orientations,
and relationship with the world. It is parallel to the constitution
(which defines what Ember does) but serves a different purpose.

The nature block is injected into the context packet every turn,
not the system prompt. See ADR-016 for the research basis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("ember.nature")


@dataclass(frozen=True)
class NatureEntry:
    id: str
    name: str
    description: str


@dataclass(frozen=True)
class Nature:
    version: str
    entries: list[NatureEntry]

    def to_prompt_text(self) -> str:
        """Render the nature block for context packet injection."""
        lines = ["Ember's nature:"]
        for entry in self.entries:
            lines.append(f"- {entry.name}: {entry.description}")
        return "\n".join(lines)


class NatureLoader:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or self._default_config_path()
        self._nature: Nature | None = None

    def load(self) -> Nature:
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Nature file not found: {self.config_path}"
            )

        with self.config_path.open("r", encoding="utf-8") as file:
            raw_data = yaml.safe_load(file)

        if not isinstance(raw_data, dict):
            raise ValueError("Nature file must contain a top-level mapping.")

        nature = self._parse_nature(raw_data)
        self._check_version(nature.version)
        self._nature = nature
        return nature

    def get_nature(self) -> Nature:
        """Return cached nature, loading if needed."""
        if self._nature is None:
            self._nature = self.load()
        return self._nature

    def to_prompt_text(self) -> str:
        """Render the nature block for context packet injection."""
        return self.get_nature().to_prompt_text()

    @staticmethod
    def _default_config_path() -> Path:
        base_dir = Path(__file__).resolve().parents[2]
        return base_dir / "config" / "nature.yaml"

    def _parse_nature(self, raw_data: dict[str, Any]) -> Nature:
        version = raw_data.get("version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("Nature file must include a non-empty 'version' string.")

        raw_entries = raw_data.get("nature")
        if not isinstance(raw_entries, list) or not raw_entries:
            raise ValueError("Nature file must include a non-empty 'nature' list.")

        entries = [self._parse_entry(item) for item in raw_entries]
        return Nature(version=version.strip(), entries=entries)

    def _parse_entry(self, raw_entry: Any) -> NatureEntry:
        if not isinstance(raw_entry, dict):
            raise ValueError("Each nature entry must be a mapping.")

        entry_id = raw_entry.get("id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise ValueError("Each nature entry must have a non-empty 'id' string.")

        name = raw_entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each nature entry must have a non-empty 'name' string.")

        description = raw_entry.get("description")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Each nature entry must have a non-empty 'description' string.")

        return NatureEntry(
            id=entry_id.strip(),
            name=name.strip(),
            description=description.strip(),
        )

    def _check_version(self, current_version: str) -> None:
        """Log a warning if the nature document version has changed."""
        version_file = self._version_file_path()
        if version_file is None:
            return

        try:
            version_file.parent.mkdir(parents=True, exist_ok=True)

            if version_file.exists():
                last_version = version_file.read_text(encoding="utf-8").strip()
                if last_version and last_version != current_version:
                    logger.warning(
                        "[NATURE] Version changed: %s → %s. "
                        "Review nature document for coherence with system prompt.",
                        last_version,
                        current_version,
                    )

            version_file.write_text(current_version, encoding="utf-8")
        except Exception as exc:
            logger.warning("[NATURE] Could not check/write version file: %s", exc)

    @staticmethod
    def _version_file_path() -> Path | None:
        """Return the path to the version tracking file, or None if vault is unavailable."""
        try:
            from src.core.config import get_private_vault_path
            vault = get_private_vault_path()
            return vault / "system" / "nature_version.txt"
        except Exception:
            return None
