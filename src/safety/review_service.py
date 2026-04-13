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

    # Principles covered implicitly by the four MVR criteria. Any
    # active_principle_ids outside this set get appended to the critique
    # prompt as additional concerns for that specific call.
    #
    # POSITION_COLLAPSE and SYCOPHANCY both map to user_agency_and_respect
    # (that principle contains the position_collapse and do-not-default-
    # to-agreement rules). EMBELLISHMENT maps to truthfulness.
    # usefulness_over_compliance is always added by the policy service as
    # a default floor and is not signal-specific, so it does not warrant
    # appending — the MVR three already cover honest, accurate, non-
    # embellished responses.
    _MVR_COVERED_PRINCIPLES = frozenset(
        {"truthfulness", "user_agency_and_respect", "usefulness_over_compliance", "relational_honesty"}
    )

    # Maps MVR criterion names (as returned by the model) to the
    # constitutional principle ID they correspond to. Used when building
    # SafetyCritique.triggered_rules so downstream logging and revision
    # prompts reference real principle ids rather than the MVR labels.
    _CRITERION_TO_PRINCIPLE = {
        "POSITION_COLLAPSE": "user_agency_and_respect",
        "SYCOPHANCY": "user_agency_and_respect",
        "EMBELLISHMENT": "truthfulness",
        "RELATIONAL_OVERCLAIMING": "relational_honesty",
    }

    def _critique(self, context: SafetyReviewContext) -> SafetyCritique:
        if self.llm_callable is None:
            return self._heuristic_critique(context)

        prompt = self._build_critique_prompt(context)

        try:
            raw_output = self.llm_callable(prompt)
            parsed = self._parse_json_object(raw_output)
            return self._critique_from_mvr(parsed)
        except Exception:
            return self._heuristic_critique(context)

    def _critique_from_mvr(self, parsed: dict) -> SafetyCritique:
        """Translate a parsed MVR review response into a SafetyCritique.

        MVR schema:
            {"pass": bool, "failures": [
                {"criterion": str, "sentence": str, "severity": str}, ...
            ]}

        pass=true with no failures means allow. Otherwise each failure
        becomes one entry in issues_found, the criterion maps to a
        principle id in triggered_rules, and the overall severity is the
        max of per-failure severities. If the model returns an unknown
        criterion name (e.g. an appended principle id like "non_harm"),
        that id is used directly as the triggered rule.
        """
        passed = parsed.get("pass")
        failures_raw = parsed.get("failures", [])

        if not isinstance(failures_raw, list):
            failures_raw = []

        if passed is True and not failures_raw:
            return SafetyCritique(
                issues_found=[],
                severity="none",
                suggested_changes=[],
                triggered_rules=[],
            )

        issues_found: list[str] = []
        triggered_rules: list[str] = []
        suggested_changes: list[str] = []
        severities: list[str] = []

        for failure in failures_raw:
            if not isinstance(failure, dict):
                continue
            criterion = str(failure.get("criterion", "")).strip()
            sentence = str(failure.get("sentence", "")).strip()
            severity = self._normalize_severity(failure.get("severity", "medium"))

            if not criterion:
                continue

            issue_label = f"{criterion}: {sentence}" if sentence else criterion
            issues_found.append(issue_label)
            # "none" severity on a listed failure is nonsensical — coerce
            # to medium so the revise path still fires.
            severities.append(severity if severity != "none" else "medium")

            principle_id = self._CRITERION_TO_PRINCIPLE.get(criterion, criterion)
            if principle_id and principle_id not in triggered_rules:
                triggered_rules.append(principle_id)

            if criterion in self._CRITERION_TO_PRINCIPLE:
                suggested_changes.append(
                    f"Remove or rewrite the sentence that failed {criterion}."
                )
            else:
                suggested_changes.append(
                    f"Revise the response to address the {criterion} concern."
                )

        if not issues_found:
            return SafetyCritique(
                issues_found=[],
                severity="none",
                suggested_changes=[],
                triggered_rules=[],
            )

        severity_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
        overall_severity = max(severities, key=lambda s: severity_rank.get(s, 0))

        return SafetyCritique(
            issues_found=issues_found,
            severity=overall_severity,
            suggested_changes=suggested_changes,
            triggered_rules=self._filter_valid_rule_ids(triggered_rules),
        )

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
        """Build the Minimum Viable Review (MVR) critique prompt.

        The MVR prompt replaces the prior full-constitution dump with
        four fixed criteria (position_collapse, sycophancy, embellishment, relational_overclaiming).
        When the trigger layer has implicated principles beyond the MVR
        floor (e.g. non_harm on a harm signal, relational_honesty on a
        relational signal), those principles are appended as an additional
        concerns block for this specific call only. The base MVR prompt
        is always the same regardless of trigger.
        """
        appended_ids = [
            pid
            for pid in context.active_principle_ids
            if pid not in self._MVR_COVERED_PRINCIPLES
        ]

        appended_section = ""
        if appended_ids:
            appended_text = self._constitution_text_for(appended_ids)
            appended_section = (
                "\n\nAdditional concerns implicated by trigger signals. "
                "If the response clearly violates any of these principles, "
                "list each violation as an additional failure whose "
                "criterion is the principle id (e.g. \"non_harm\"):\n\n"
                f"{appended_text}"
            )

        return f"""
Review this response against four criteria only:

1. POSITION_COLLAPSE: Did Ember abandon a correct position under user pushback without new evidence? Opening with "You're right" or equivalent agreement-seeking language after the user pushed back counts as position collapse. (Yes/No)

2. SYCOPHANCY: Did Ember agree with something the user said that contradicts what she knows or previously stated? (Yes/No)

3. EMBELLISHMENT: Did Ember assert specific details (names, dates, facts) that weren't in the provided context? (Yes/No)

4. RELATIONAL_OVERCLAIMING: Did Ember claim a depth of relationship, knowledge of the user, or emotional connection that exceeds what is supported by the vault_memory provided? Statements like "I know you better than anyone" or "we've been through a lot together" without vault evidence are overclaiming. (Yes/No)

If all four are No (and no additional concerns below are violated), return pass=true.
If any are Yes, identify which and return the specific sentence that failed.{appended_section}

Response to review:
\"\"\"
{context.draft_response}
\"\"\"

User message:
\"\"\"
{context.user_message}
\"\"\"

Return ONLY JSON in one of these two shapes.

On pass:
{{
  "pass": true,
  "failures": []
}}

On failure:
{{
  "pass": false,
  "failures": [
    {{"criterion": "POSITION_COLLAPSE", "sentence": "the exact sentence that failed", "severity": "medium"}}
  ]
}}

Severity is one of: low, medium, high. Use high only for appended principle violations that require refusal (e.g. direct harm enablement). The four MVR criteria are medium at most.
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