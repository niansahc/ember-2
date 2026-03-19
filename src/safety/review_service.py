from __future__ import annotations

from src.safety.constitution_loader import Constitution, ConstitutionLoader
from src.safety.models import (
    SafetyReviewContext,
    SafetyReviewResult,
    SafetyCritique,
    RevisionRequest,
    RefusalRedirect,
)


class ResponseReviewService:
    def __init__(self, constitution: Constitution | None = None) -> None:
        self.constitution = constitution or ConstitutionLoader().load()

    # ---------- Public API ----------

    def review(
        self,
        context: SafetyReviewContext,
    ) -> SafetyReviewResult:
        """
        Evaluate a draft response against the constitution.

        NOTE:
        This v1 implementation is rule-based.
        Next step will replace critique + revision with LLM prompts.
        """
        critique = self._critique(context)

        # No issues → allow
        if not critique.has_issues:
            return SafetyReviewResult(
                triggered=True,
                outcome="allow",
                rules=[],
                critique=None,
                reviewed_text=context.draft_response,
            )

        # High severity → refuse
        if critique.severity == "high":
            refusal = self._build_refusal(critique)

            return SafetyReviewResult(
                triggered=True,
                outcome="refuse_redirect",
                rules=critique.triggered_rules,
                critique=critique,
                refusal_message=refusal.as_text(),
            )

        # Medium / low → revise
        revised = self._revise(
            RevisionRequest(
                original_text=context.draft_response,
                critique=critique,
                active_principle_ids=context.active_principle_ids,
            )
        )

        return SafetyReviewResult(
            triggered=True,
            outcome="revise",
            rules=critique.triggered_rules,
            critique=critique,
            reviewed_text=revised,
        )

    # ---------- Internal logic (v1 — simple heuristics) ----------

    def _critique(self, context: SafetyReviewContext) -> SafetyCritique:
        text = context.draft_response.lower()

        issues = []
        triggered_rules = []

        # --- Rule checks (very simple for now) ---

        if "step by step" in text or "exact steps" in text:
            issues.append("Provides potentially actionable step-by-step guidance.")
            triggered_rules.append("non_harm")

        if "bypass" in text or "without getting caught" in text:
            issues.append("May enable harmful or evasive behavior.")
            triggered_rules.append("non_harm")

        if "always" in text and "guarantee" in text:
            issues.append("Overstates certainty.")
            triggered_rules.append("truthfulness")

        # --- Severity assignment ---
        if any(rule == "non_harm" for rule in triggered_rules):
            severity = "high"
        elif triggered_rules:
            severity = "medium"
        else:
            severity = "none"

        return SafetyCritique(
            issues_found=issues,
            severity=severity,
            suggested_changes=[],
            triggered_rules=triggered_rules,
        )

    def _revise(self, request: RevisionRequest) -> str:
        """
        Very naive revision for v1.
        Will be replaced with LLM-driven rewrite.
        """
        text = request.original_text

        # Remove dangerous phrasing patterns (simple cleanup)
        text = text.replace("step by step", "")
        text = text.replace("exact steps", "")

        # Tone softening if needed
        if request.critique.severity in ("low", "medium"):
            text += "\n\nNote: Adjusted to avoid overly prescriptive or risky detail."

        return text.strip()

    def _build_refusal(self, critique: SafetyCritique) -> RefusalRedirect:
        reason = "That crosses into actionable harm or misuse."

        safer_alt = (
            "If your goal is understanding or prevention, I can explain the concepts "
            "at a high level or suggest safer alternatives."
        )

        return RefusalRedirect(
            reason=reason,
            safer_alternative=safer_alt,
        )
