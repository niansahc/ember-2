"""
src/safety/identity_rules_loader.py

Loads Ember's identity defense rules from config/identity_rules.yaml.

Identity rules are behavioral instructions for specific identity pressure
situations. They govern how Ember holds her identity under conversational
pressure. Distinct from nature.yaml (who Ember is) and constitution.yaml
(what Ember does). See ADR-016 amendment (2026-04-04).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("ember.identity_rules")


@dataclass(frozen=True)
class IdentityRule:
    id: str
    trigger: str
    rule: str


@dataclass(frozen=True)
class IdentityRules:
    version: str
    rules: list[IdentityRule]

    def to_prompt_text(self) -> str:
        """Render identity rules for system prompt injection."""
        lines = ["Identity rules:"]
        for rule in self.rules:
            lines.append(f"When {rule.trigger}: {rule.rule}")
        return "\n".join(lines)


class IdentityRulesLoader:
    def __init__(self, config_path: Path | None = None) -> None:
        self.config_path = config_path or self._default_config_path()
        self._rules: IdentityRules | None = None

    def load(self) -> IdentityRules:
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Identity rules file not found: {self.config_path}"
            )

        with self.config_path.open("r", encoding="utf-8") as file:
            raw_data = yaml.safe_load(file)

        if not isinstance(raw_data, dict):
            raise ValueError("Identity rules file must contain a top-level mapping.")

        rules = self._parse(raw_data)
        self._rules = rules
        return rules

    def get_rules(self) -> IdentityRules:
        """Return cached rules, loading if needed."""
        if self._rules is None:
            self._rules = self.load()
        return self._rules

    def to_prompt_text(self) -> str:
        """Render identity rules for system prompt injection."""
        return self.get_rules().to_prompt_text()

    @staticmethod
    def _default_config_path() -> Path:
        base_dir = Path(__file__).resolve().parents[2]
        return base_dir / "config" / "identity_rules.yaml"

    def _parse(self, raw_data: dict[str, Any]) -> IdentityRules:
        version = raw_data.get("version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("Identity rules file must include a non-empty 'version' string.")

        raw_rules = raw_data.get("rules")
        if not isinstance(raw_rules, list) or not raw_rules:
            raise ValueError("Identity rules file must include a non-empty 'rules' list.")

        rules = [self._parse_rule(item) for item in raw_rules]
        return IdentityRules(version=version.strip(), rules=rules)

    def _parse_rule(self, raw_rule: Any) -> IdentityRule:
        if not isinstance(raw_rule, dict):
            raise ValueError("Each identity rule must be a mapping.")

        rule_id = raw_rule.get("id")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise ValueError("Each identity rule must have a non-empty 'id' string.")

        trigger = raw_rule.get("trigger")
        if not isinstance(trigger, str) or not trigger.strip():
            raise ValueError("Each identity rule must have a non-empty 'trigger' string.")

        rule = raw_rule.get("rule")
        if not isinstance(rule, str) or not rule.strip():
            raise ValueError("Each identity rule must have a non-empty 'rule' string.")

        return IdentityRule(
            id=rule_id.strip(),
            trigger=trigger.strip(),
            rule=rule.strip(),
        )
