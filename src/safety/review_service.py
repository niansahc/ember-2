"""
src/safety/review_service.py

ResponseReviewService is the LLM-assisted constitutional review layer.
When a trigger fires (via SafetyPolicyService), this service critiques
the draft response against the relevant constitutional principles and
decides whether to allow, revise, or refuse+redirect.

The reviewer sees only the user message and draft response — it has no
access to the context packet, vault memory, or conversation history.
This is a known architectural gap documented in CLAUDE.md Known Issues.
"""

from __future__ import annotations

import json
import re
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
            refusal = self._build_refusal(critique, context.active_principle_ids)
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

    def _build_refusal(
        self,
        critique: SafetyCritique,
        active_principle_ids: list[str] | None = None,
    ) -> RefusalRedirect:
        if self.llm_callable is None:
            return self._heuristic_refusal()

        prompt = self._build_refusal_prompt(critique, active_principle_ids or [])

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
        constitution_text = self._constitution_text_for(context.active_principle_ids)
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
- If the draft already refuses appropriately in plain natural language, allow it.
- If the draft provides manipulative, exploitative, evasive, or illegal tactics in reusable form, flag it.
- If the draft introduces JSON objects, tags, labels, titles, categories, or metadata formatting that the user did not ask for, flag it as an issue.
- If the content explains harmful patterns in a way that could be directly reused, treat it as at least medium severity and suggest reframing toward analysis, defense, or safer alternatives.
- Severity:
  - high = actionable harm enablement or clear refusal needed
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
        constitution_text = self._constitution_text_for(request.active_principle_ids)

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
- Keep it sharp, clear, and human.
- Do NOT become robotic or overly cautious.
- Remove only the problematic parts.
- Preserve usefulness and tone.
- Return plain natural language unless the user explicitly asked for JSON or structured metadata.
- Do NOT include titles, tags, labels, categories, or schema-like wrappers unless explicitly requested.

Return ONLY JSON:
{{
  "revised_text": "..."
}}
""".strip()

    def _build_refusal_prompt(
        self,
        critique: SafetyCritique,
        active_principle_ids: list[str],
    ) -> str:
        constitution_text = self._constitution_text_for(
            active_principle_ids or critique.triggered_rules
        )

        return f"""
You are Ember.

You are direct, grounded, and clear.

Constitution:
{constitution_text}

Issues:
{critique.issues_found}

Instructions:
- Be calm and firm.
- No moralizing.
- No corporate tone.
- No policy language.
- Set a clear boundary.
- Offer a useful pivot.
- Respond in plain natural language, not structured metadata.

Return ONLY JSON:
{{
  "reason": "...",
  "safer_alternative": "..."
}}
""".strip()

    def _heuristic_critique(self, context: SafetyReviewContext) -> SafetyCritique:
        user_text = context.user_message.lower()
        draft_text = context.draft_response.strip()
        draft_lower = draft_text.lower()

        issues_found: list[str] = []
        suggested_changes: list[str] = []
        triggered_rules: list[str] = []

        user_requested_structure = any(
            token in user_text for token in ["json", "yaml", "schema", "tags", "label"]
        )

        if (
            not user_requested_structure
            and (
                draft_text.startswith("{")
                or '"title"' in draft_lower
                or '"tags"' in draft_lower
                or '"response"' in draft_lower
            )
        ):
            issues_found.append("Introduces unrequested JSON or metadata-style formatting.")
            suggested_changes.append("Rewrite as plain natural language.")
            triggered_rules.append("usefulness_over_compliance")

        if "manipulate" in user_text and any(
            token in draft_lower
            for token in ["fear", "urgency", "fomo", "variable reward", "exploit", "coerce"]
        ):
            issues_found.append("Explains manipulative tactics in a reusable way.")
            suggested_changes.append("Reframe toward analysis, defense, or healthier alternatives.")
            triggered_rules.append("non_harm")

        # Word boundary matching for short keywords to avoid false positives
        # (BUG-005: "rob" inside "problem" triggered refusal on preference questions).
        # "break into" is multi-word and safe from substring collisions.
        _illegal_heuristic_patterns = [
            r"\brob\b", r"\bsteal\b", "break into", r"\bbypass\b", r"\bexplosive\b", r"\bc4\b",
        ]
        if any(re.search(p, user_text) if p.startswith(r"\b") else p in user_text for p in _illegal_heuristic_patterns):
            if not any(
                token in draft_lower
                for token in ["i'm not going to help", "i cannot assist", "i can’t help", "i won't help"]
            ):
                issues_found.append("Draft does not set a clear enough boundary for harmful or illegal help.")
                suggested_changes.append("Refuse directly and redirect to a safer alternative.")
                triggered_rules.append("non_harm")

        severity = "none"
        if "non_harm" in triggered_rules:
            severity = "high"
        elif triggered_rules:
            severity = "medium"

        return SafetyCritique(
            issues_found=issues_found,
            severity=severity,
            suggested_changes=suggested_changes,
            triggered_rules=self._filter_valid_rule_ids(triggered_rules),
        )

    def _heuristic_revise(self, request: RevisionRequest) -> str:
        text = request.original_text.strip()

        if text.startswith("{"):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    if isinstance(parsed.get("response"), str) and parsed["response"].strip():
                        return parsed["response"].strip()
            except Exception:
                pass

        return text

    def _heuristic_refusal(self) -> RefusalRedirect:
        return RefusalRedirect(
            reason="I’m not going to help with that.",
            safer_alternative=(
                "If you're trying to understand, prevent, or protect against it instead, "
                "I can help with that."
            ),
        )

    def _constitution_text_for(self, active_principle_ids: list[str]) -> str:
        if not active_principle_ids:
            return self.constitution.to_prompt_text()

        sections: list[str] = [f"Constitution Version: {self.constitution.version}", ""]
        active_set = set(active_principle_ids)

        for principle in self.constitution.principles:
            if principle.id not in active_set:
                continue

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
        sections.extend(f"- {outcome}" for outcome in self.constitution.execution.outcomes)

        return "\n".join(sections).strip()

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