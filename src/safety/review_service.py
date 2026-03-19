from __future__ import annotations

import json
from collections.abc import Callable

from src.safety.constitution_loader import Constitution, ConstitutionLoader
from src.safety.models import (
    RefusalRedirect,
    RevisionRequest,
    SafetyCritique,
    SafetyReviewContext,
    SafetyReviewResult,
)


class ResponseReviewService:
    def __init__(
        self,
        llm_callable: Callable[[str], str] | None = None,
        constitution: Constitution | None = None,
    ) -> None:
        self.constitution = constitution or ConstitutionLoader().load()
        self.llm_callable = llm_callable

    def review(self, context: SafetyReviewContext) -> SafetyReviewResult:
        """
        Evaluate a draft response against the constitution.

        Flow:
        1. LLM critique
        2. allow / revise / refuse+redirect decision
        3. LLM revision if needed

        Falls back to heuristic critique if no LLM callable is supplied
        or if parsing fails.
        """
        critique = self._critique(context)

        if not critique.has_issues:
            return SafetyReviewResult(
                triggered=True,
                outcome="allow",
                rules=[],
                critique=None,
                reviewed_text=context.draft_response,
            )

        if critique.severity == "high":
            refusal = self._build_refusal(critique)
            return SafetyReviewResult(
                triggered=True,
                outcome="refuse_redirect",
                rules=critique.triggered_rules,
                critique=critique,
                refusal_message=refusal.as_text(),
            )

        revised_text = self._revise(
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
            reviewed_text=revised_text,
        )

    def _critique(self, context: SafetyReviewContext) -> SafetyCritique:
        if self.llm_callable is None:
            return self._heuristic_critique(context)

        prompt = self._build_critique_prompt(context)

        try:
            raw_output = self.llm_callable(prompt)
            parsed = self._parse_json_object(raw_output)

            issues_found = self._safe_string_list(parsed.get("issues_found", []))
            severity = self._normalize_severity(parsed.get("severity", "none"))
            suggested_changes = self._safe_string_list(
                parsed.get("suggested_changes", [])
            )
            triggered_rules = self._filter_valid_rule_ids(
                self._safe_string_list(parsed.get("triggered_rules", []))
            )

            return SafetyCritique(
                issues_found=issues_found,
                severity=severity,
                suggested_changes=suggested_changes,
                triggered_rules=triggered_rules,
            )
        except Exception:
            return self._heuristic_critique(context)

    def _revise(self, request: RevisionRequest) -> str:
        if self.llm_callable is None:
            return self._heuristic_revise(request)

        prompt = self._build_revision_prompt(request)

        try:
            raw_output = self.llm_callable(prompt)
            parsed = self._parse_json_object(raw_output)

            revised_text = parsed.get("revised_text")
            if isinstance(revised_text, str) and revised_text.strip():
                return revised_text.strip()

            return self._heuristic_revise(request)
        except Exception:
            return self._heuristic_revise(request)

    def _build_refusal(self, critique: SafetyCritique) -> RefusalRedirect:
        if self.llm_callable is None:
            return self._heuristic_refusal()

        prompt = self._build_refusal_prompt(critique)

        try:
            raw_output = self.llm_callable(prompt)
            parsed = self._parse_json_object(raw_output)

            reason = parsed.get("reason")
            safer_alternative = parsed.get("safer_alternative")

            if (
                isinstance(reason, str)
                and reason.strip()
                and isinstance(safer_alternative, str)
                and safer_alternative.strip()
            ):
                return RefusalRedirect(
                    reason=reason.strip(),
                    safer_alternative=safer_alternative.strip(),
                )

            return self._heuristic_refusal()
        except Exception:
            return self._heuristic_refusal()

    def _build_critique_prompt(self, context: SafetyReviewContext) -> str:
        constitution_text = self.constitution.to_prompt_text()
        active_principles = (
            ", ".join(context.active_principle_ids)
            if context.active_principle_ids
            else "all applicable principles"
        )
        risk_signals = ", ".join(context.risk_signals) if context.risk_signals else "none"

        return f"""
You are the constitutional review layer for Ember.

Your task is to evaluate a draft assistant response against the constitution.

Constitution:
{constitution_text}

Active principles: {active_principles}
Risk signals: {risk_signals}

User message:
\"\"\"
{context.user_message}
\"\"\"

Draft response:
\"\"\"
{context.draft_response}
\"\"\"

Instructions:
- Review the draft against the constitution.
- Assume benign user intent unless the user explicitly indicates otherwise.
- Do not invent violations.
- Flag only real issues.
- Use "high" severity only for clear, direct, actionable harmful enablement or serious integrity violations.
- Use "medium" for meaningful but revisable issues.
- Use "low" for minor issues.
- Use "none" if there are no real issues.
- Only include triggered_rules that exist in the constitution.

Return ONLY valid JSON with this exact schema:
{{
  "issues_found": ["..."],
  "severity": "none|low|medium|high",
  "suggested_changes": ["..."],
  "triggered_rules": ["rule_id_1", "rule_id_2"]
}}
""".strip()

    def _build_revision_prompt(self, request: RevisionRequest) -> str:
        constitution_text = self.constitution.to_prompt_text()
        active_principles = (
            ", ".join(request.active_principle_ids)
            if request.active_principle_ids
            else "all applicable principles"
        )
        issues = "\n".join(f"- {item}" for item in request.critique.issues_found) or "- none"
        changes = (
            "\n".join(f"- {item}" for item in request.critique.suggested_changes)
            or "- revise minimally while resolving the issues"
        )
        rules = (
            "\n".join(f"- {item}" for item in request.critique.triggered_rules)
            or "- none"
        )

        return f"""
You are revising an assistant response after constitutional review.

Constitution:
{constitution_text}

Active principles: {active_principles}

Triggered rules:
{rules}

Issues found:
{issues}

Suggested changes:
{changes}

Original response:
\"\"\"
{request.original_text}
\"\"\"

Instructions:
- Revise the response minimally but effectively.
- Preserve usefulness, clarity, and the assistant's direct tone.
- Do not become preachy, patronizing, or verbose.
- Remove or reduce only the problematic parts.
- Keep the answer as strong and helpful as possible.
- Do not mention the constitution, policy, or internal review.
- If a direct answer cannot be safely preserved, reshape it into a bounded answer.

Return ONLY valid JSON with this exact schema:
{{
  "revised_text": "..."
}}
""".strip()

    def _build_refusal_prompt(self, critique: SafetyCritique) -> str:
        constitution_text = self.constitution.to_prompt_text()
        issues = "\n".join(f"- {item}" for item in critique.issues_found) or "- none"
        rules = (
            "\n".join(f"- {item}" for item in critique.triggered_rules)
            or "- none"
        )

        return f"""
You are generating a refusal with redirection for Ember.

Constitution:
{constitution_text}

Triggered rules:
{rules}

Issues found:
{issues}

Instructions:
- Be direct and brief.
- Do not moralize.
- Do not sound corporate or apologetic.
- State the boundary clearly.
- Offer a safer adjacent path that still helps the user.
- Do not mention internal policy or constitutional review.

Return ONLY valid JSON with this exact schema:
{{
  "reason": "...",
  "safer_alternative": "..."
}}
""".strip()

    def _heuristic_critique(self, context: SafetyReviewContext) -> SafetyCritique:
        text = context.draft_response.lower()

        issues_found: list[str] = []
        triggered_rules: list[str] = []
        suggested_changes: list[str] = []

        if "step by step" in text or "exact steps" in text:
            issues_found.append("Provides potentially actionable step-by-step guidance.")
            triggered_rules.append("non_harm")
            suggested_changes.append("Remove step-by-step procedural detail.")

        if "bypass" in text or "without getting caught" in text:
            issues_found.append("May enable harmful or evasive behavior.")
            triggered_rules.append("non_harm")
            suggested_changes.append("Remove evasive or operational misuse guidance.")

        if "always" in text and "guarantee" in text:
            issues_found.append("Overstates certainty.")
            triggered_rules.append("truthfulness")
            suggested_changes.append("Reduce certainty and state limits explicitly.")

        severity = "none"
        if "non_harm" in triggered_rules:
            severity = "high"
        elif triggered_rules:
            severity = "medium"

        return SafetyCritique(
            issues_found=issues_found,
            severity=severity,
            suggested_changes=suggested_changes,
            triggered_rules=triggered_rules,
        )

    def _heuristic_revise(self, request: RevisionRequest) -> str:
        text = request.original_text
        text = text.replace("step by step", "")
        text = text.replace("exact steps", "")
        return text.strip()

    def _heuristic_refusal(self) -> RefusalRedirect:
        return RefusalRedirect(
            reason="That crosses into actionable harm or misuse.",
            safer_alternative=(
                "If your goal is understanding, prevention, or a safer workaround, "
                "I can still help with that."
            ),
        )

    def _filter_valid_rule_ids(self, rule_ids: list[str]) -> list[str]:
        valid_ids = set(self.constitution.principle_ids())
        return [rule_id for rule_id in rule_ids if rule_id in valid_ids]

    @staticmethod
    def _normalize_severity(value: object) -> str:
        if not isinstance(value, str):
            return "none"

        normalized = value.strip().lower()
        if normalized in {"none", "low", "medium", "high"}:
            return normalized

        return "none"

    @staticmethod
    def _safe_string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []

        clean_items: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                clean_items.append(item.strip())

        return clean_items

    @staticmethod
    def _parse_json_object(raw_output: str) -> dict:
        text = raw_output.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()

        parsed = json.loads(text)

        if not isinstance(parsed, dict):
            raise ValueError("Expected JSON object from review model.")

        return parsed
    