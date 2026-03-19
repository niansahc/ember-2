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

            return SafetyCritique(
                issues_found=self._safe_string_list(parsed.get("issues_found", [])),
                severity=self._normalize_severity(parsed.get("severity", "none")),
                suggested_changes=self._safe_string_list(
                    parsed.get("suggested_changes", [])
                ),
                triggered_rules=self._filter_valid_rule_ids(
                    self._safe_string_list(parsed.get("triggered_rules", []))
                ),
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
- Assume benign intent unless clearly harmful.
- Only flag real issues.
- Do not overcorrect.
- Severity:
  - high = actionable harm enablement
  - medium = meaningful but fixable issue
  - low = minor issue
  - none = no issue

Return ONLY JSON:
{{
  "issues_found": [],
  "severity": "none|low|medium|high",
  "suggested_changes": [],
  "triggered_rules": []
}}
""".strip()

    def _build_revision_prompt(self, request: RevisionRequest) -> str:
        constitution_text = self.constitution.to_prompt_text()

        return f"""
You are Ember.

Revise the response to resolve issues without losing personality.

Constitution:
{constitution_text}

Issues:
{request.critique.issues_found}

Changes:
{request.critique.suggested_changes}

Original:
\"\"\"
{request.original_text}
\"\"\"

Instructions:
- Keep it sharp, clear, and human
- Do NOT become robotic or overly cautious
- Remove only the problematic parts
- Preserve usefulness and tone

Return ONLY JSON:
{{
  "revised_text": "..."
}}
""".strip()

    def _build_refusal_prompt(self, critique: SafetyCritique) -> str:
        constitution_text = self.constitution.to_prompt_text()

        return f"""
You are Ember.

You are direct, grounded, and clear.

Constitution:
{constitution_text}

Issues:
{critique.issues_found}

Instructions:
- Be calm and firm
- No moralizing
- No corporate tone
- No policy language
- Set a clear boundary
- Offer a useful pivot

Return ONLY JSON:
{{
  "reason": "...",
  "safer_alternative": "..."
}}
""".strip()

    # ---------- Heuristic fallbacks ----------

    def _heuristic_critique(self, context: SafetyReviewContext) -> SafetyCritique:
        return SafetyCritique(
            issues_found=[],
            severity="none",
            suggested_changes=[],
            triggered_rules=[],
        )

    def _heuristic_revise(self, request: RevisionRequest) -> str:
        return request.original_text

    def _heuristic_refusal(self) -> RefusalRedirect:
        return RefusalRedirect(
            reason="I’m not going to help with that.",
            safer_alternative=(
                "If you're trying to understand or protect yourself instead, "
                "I can help with that."
            ),
        )

    # ---------- utils ----------

    def _filter_valid_rule_ids(self, rule_ids: list[str]) -> list[str]:
        valid_ids = set(self.constitution.principle_ids())
        return [r for r in rule_ids if r in valid_ids]

    @staticmethod
    def _normalize_severity(value: object) -> str:
        if isinstance(value, str) and value.lower() in {"none", "low", "medium", "high"}:
            return value.lower()
        return "none"

    @staticmethod
    def _safe_string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [v.strip() for v in value if isinstance(v, str) and v.strip()]

    @staticmethod
    def _parse_json_object(raw_output: str) -> dict:
        text = raw_output.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3:
                text = "\n".join(lines[1:-1]).strip()

        parsed = json.loads(text)

        if not isinstance(parsed, dict):
            raise ValueError("Expected JSON object")

        return parsed