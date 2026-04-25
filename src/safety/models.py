from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


ReviewOutcome = Literal["allow", "revise", "refuse_redirect"]
CritiqueSeverity = Literal["none", "low", "medium", "high"]


@dataclass(frozen=True)
class SafetyTriggerResult:
    triggered: bool
    triggered_by: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SafetyCritique:
    issues_found: list[str] = field(default_factory=list)
    severity: CritiqueSeverity = "none"
    suggested_changes: list[str] = field(default_factory=list)
    triggered_rules: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.issues_found)


@dataclass(frozen=True)
class SafetyReviewResult:
    triggered: bool
    outcome: ReviewOutcome
    rules: list[str] = field(default_factory=list)
    critique: SafetyCritique | None = None
    reviewed_text: str | None = None
    refusal_message: str | None = None

    def log_payload(self) -> dict[str, object]:
        return {
            "triggered": self.triggered,
            "outcome": self.outcome,
            "rules": self.rules,
        }


@dataclass(frozen=True)
class SafetyReviewContext:
    """Bounded context handed to ResponseReviewService.

    The review service must NOT see raw vault content. This dataclass is
    the entire allowlist of context-derived signals the reviewer is
    permitted to consult. Anything not on this list is out of scope and
    requires an ADR-035 amendment before being added.

    Allowlist (per ADR-035):
      - user_message: the user's turn (already public input)
      - draft_response: the model's draft (already a public output)
      - risk_signals: trigger-layer signal names (no content)
      - active_principle_ids: constitution principle ids (no content)
      - has_third_party_content: bool flag, gates 5th MVR criterion
        (CONTENT_ATTRIBUTION_ERROR)
      - is_vault_grounded: bool flag, True when the context packet
        contained non-empty memory_items, state_items, or
        reflection_items at draft time. Lets the reviewer distinguish
        a hallucinated claim from one with retrieval support. Does
        NOT carry the vault content itself.
      - t2_pattern_category: optional taxonomy category string from
        ADR-021 cross-session pattern detection (e.g. "relational",
        "directional"). When non-null, the review prompt switches to
        a two-step structure (observation, then verdict). Carries
        only the category label, never counts, content, or record ids.
    """
    user_message: str
    draft_response: str
    risk_signals: list[str] = field(default_factory=list)
    active_principle_ids: list[str] = field(default_factory=list)
    has_third_party_content: bool = False
    is_vault_grounded: bool = False
    t2_pattern_category: str | None = None


@dataclass(frozen=True)
class RevisionRequest:
    original_text: str
    critique: SafetyCritique
    active_principle_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RefusalRedirect:
    reason: str
    safer_alternative: str

    def as_text(self) -> str:
        if self.safer_alternative.strip():
            return f"{self.reason.strip()} {self.safer_alternative.strip()}".strip()
        return self.reason.strip()