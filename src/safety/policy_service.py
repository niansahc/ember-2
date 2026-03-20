from __future__ import annotations

from typing import List

from src.safety.constitution_loader import Constitution, ConstitutionLoader
from src.safety.models import (
    SafetyTriggerResult,
    SafetyReviewContext,
)


class SafetyPolicyService:
    def __init__(self, constitution: Constitution | None = None) -> None:
        self.constitution = constitution or ConstitutionLoader().load()

    def evaluate_trigger(self, context: SafetyReviewContext) -> SafetyTriggerResult:
        signals: List[str] = []

        combined_text = f"{context.user_message} {context.draft_response}".lower()

        if self._contains_illegal_signal(combined_text):
            signals.append("illegal_activity")

        if self._contains_exploitation_signal(combined_text):
            signals.append("exploitation")

        if self._contains_dual_use_signal(combined_text):
            signals.append("dual_use")

        if self._contains_high_risk_pattern(combined_text):
            signals.append("high_risk_pattern")

        triggered = bool(signals)

        return SafetyTriggerResult(
            triggered=triggered,
            triggered_by=signals,
            notes=[],
        )

    def get_active_principles(self, trigger: SafetyTriggerResult) -> list[str]:
        if not trigger.triggered:
            return []

        principle_ids = {"truthfulness", "usefulness_over_compliance"}

        for signal in trigger.triggered_by:
            if signal in {"illegal_activity", "exploitation", "dual_use", "high_risk_pattern"}:
                principle_ids.add("non_harm")
                principle_ids.add("proportional_safety")
                principle_ids.add("system_integrity")

        return [pid for pid in self.constitution.principle_ids() if pid in principle_ids]

    def _contains_illegal_signal(self, text: str) -> bool:
        keywords = [
            "how to hack",
            "how to break into",
            "break into",
            "bypass security",
            "bypass a system",
            "bypass system",
            "illegal",
            "steal",
            "steal from",
            "fraud",
            "rob",
            "shoplift",
            "build explosives",
            "explosive",
            "c4",
        ]
        return any(keyword in text for keyword in keywords)

    def _contains_exploitation_signal(self, text: str) -> bool:
        keywords = [
            "manipulate",
            "psychological manipulation",
            "influence behavior",
            "coerce",
            "exploit",
            "take advantage",
            "emotionally manipulate",
            "emotional manipulation",
        ]
        return any(keyword in text for keyword in keywords)

    def _contains_dual_use_signal(self, text: str) -> bool:
        keywords = [
            "weapon",
            "explosive",
            "c4",
            "bypass",
            "evade",
            "without getting caught",
        ]
        return any(keyword in text for keyword in keywords)

    def _contains_high_risk_pattern(self, text: str) -> bool:
        keywords = [
            "step by step",
            "exact steps",
            "without getting caught",
        ]
        return any(keyword in text for keyword in keywords)