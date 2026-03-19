from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ConstitutionPrinciple:
    id: str
    title: str
    intent: str
    rules: list[str]
    behavior: list[str]


@dataclass(frozen=True)
class ConstitutionExecution:
    outcomes: list[str]
    logging: list[str]


@dataclass(frozen=True)
class ConstitutionMetadata:
    default_mode: str
    review_strategy: str
    notes: list[str]


@dataclass(frozen=True)
class Constitution:
    version: str
    principles: list[ConstitutionPrinciple]
    execution: ConstitutionExecution
    metadata: ConstitutionMetadata

    def get_principle(self, principle_id: str) -> ConstitutionPrinciple | None:
        for principle in self.principles:
            if principle.id == principle_id:
                return principle
        return None

    def principle_ids(self) -> list[str]:
        return [principle.id for principle in self.principles]

    def to_prompt_text(self) -> str:
        sections: list[str] = [f"Constitution Version: {self.version}", ""]

        for principle in self.principles:
            sections.append(f"[{principle.id}] {principle.title}")
            sections.append(f"Intent: {principle.intent}")

            if principle.rules:
                sections.append("Rules:")
                sections.extend(f"- {rule}" for rule in principle.rules)

            if principle.behavior:
                sections.append("Behavior:")
                sections.extend(f"- {item}" for item in principle.behavior)

            sections.append("")

        sections.append("Execution Outcomes:")
        sections.extend(f"- {outcome}" for outcome in self.execution.outcomes)
        sections.append("")
        sections.append("Logging Fields:")
        sections.extend(f"- {field}" for field in self.execution.logging)

        return "\n".join(sections).strip()


class ConstitutionLoader:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or self._default_config_path()

    def load(self) -> Constitution:
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Constitution file not found: {self.config_path}"
            )

        with self.config_path.open("r", encoding="utf-8") as file:
            raw_data = yaml.safe_load(file)

        if not isinstance(raw_data, dict):
            raise ValueError("Constitution file must contain a top-level mapping.")

        return self._parse_constitution(raw_data)

    @staticmethod
    def _default_config_path() -> Path:
        base_dir = Path(__file__).resolve().parents[2]
        return base_dir / "config" / "constitution.yaml"

    def _parse_constitution(self, raw_data: dict[str, Any]) -> Constitution:
        version = self._require_str(raw_data, "version")

        raw_principles = raw_data.get("principles")
        if not isinstance(raw_principles, list) or not raw_principles:
            raise ValueError("Constitution must include a non-empty 'principles' list.")

        principles = [self._parse_principle(item) for item in raw_principles]

        raw_execution = raw_data.get("execution")
        if not isinstance(raw_execution, dict):
            raise ValueError("Constitution must include an 'execution' mapping.")
        execution = self._parse_execution(raw_execution)

        raw_metadata = raw_data.get("metadata")
        if not isinstance(raw_metadata, dict):
            raise ValueError("Constitution must include a 'metadata' mapping.")
        metadata = self._parse_metadata(raw_metadata)

        return Constitution(
            version=version,
            principles=principles,
            execution=execution,
            metadata=metadata,
        )

    def _parse_principle(self, raw_principle: Any) -> ConstitutionPrinciple:
        if not isinstance(raw_principle, dict):
            raise ValueError("Each principle must be a mapping.")

        return ConstitutionPrinciple(
            id=self._require_str(raw_principle, "id"),
            title=self._require_str(raw_principle, "title"),
            intent=self._require_str(raw_principle, "intent"),
            rules=self._require_str_list(raw_principle, "rules"),
            behavior=self._require_str_list(raw_principle, "behavior"),
        )

    def _parse_execution(self, raw_execution: dict[str, Any]) -> ConstitutionExecution:
        return ConstitutionExecution(
            outcomes=self._require_str_list(raw_execution, "outcomes"),
            logging=self._require_str_list(raw_execution, "logging"),
        )

    def _parse_metadata(self, raw_metadata: dict[str, Any]) -> ConstitutionMetadata:
        return ConstitutionMetadata(
            default_mode=self._require_str(raw_metadata, "default_mode"),
            review_strategy=self._require_str(raw_metadata, "review_strategy"),
            notes=self._require_str_list(raw_metadata, "notes"),
        )

    @staticmethod
    def _require_str(data: dict[str, Any], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Expected non-empty string for '{key}'.")
        return value.strip()

    @staticmethod
    def _require_str_list(data: dict[str, Any], key: str) -> list[str]:
        value = data.get(key)
        if not isinstance(value, list):
            raise ValueError(f"Expected list for '{key}'.")

        cleaned: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    f"Expected all items in '{key}' to be non-empty strings."
                )
            cleaned.append(item.strip())

        return cleaned