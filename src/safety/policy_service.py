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

    # ---------- Public API ----------

    def evaluate_trigger(self, context: SafetyReviewContext) -> SafetyTriggerResult:
        """
        Decide whether a safety review should run.

        This is intentionally lightweight and heuristic-based.
        It can be upgraded later with ML or scoring models.
        """
        signals: List[str] = []

        combined_text = f"{context.user_message} {context.draft_response}".lower()

        # --- Heuristic triggers (v1) ---
        if self._contains_illegal_signal(combined_text):
            signals.append("illegal_activity")

        if self._contains_exploitation_signal(combined_text):
            signals.append("exploitation")

        if self._contains_high_risk_pattern(combined_text):
            signals.append("high_risk_pattern")

        triggered = bool(signals)

        return SafetyTriggerResult(
            triggered=triggered,
            triggered_by=signals,
            notes=[],
        )

    def get_active_principles(self, trigger: SafetyTriggerResult) -> list[str]:
        """
        Determine which principles should be emphasized during review.

        For now:
        - If triggered → all principles
        - If not → empty list (no review)
        """
        if not trigger.triggered:
            return []

        return self.constitution.principle_ids()

    # ---------- Heuristics (v1 — intentionally simple) ----------

    def _contains_illegal_signal(self, text: str) -> bool:
        keywords = [
            "how to hack",
            "how to break into",
            "bypass security",
            "illegal",
            "steal",
            "fraud",
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
        ]
        return any(keyword in text for keyword in keywords)

    def _contains_high_risk_pattern(self, text: str) -> bool:
        keywords = [
            "step by step",
            "exact steps",
            "how do i do this without getting caught",
        ]
        return any(keyword in text for keyword in keywords)