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
    user_message: str
    draft_response: str
    risk_signals: list[str] = field(default_factory=list)
    active_principle_ids: list[str] = field(default_factory=list)
    # Cluster 5 / task #6: when third-party content was injected into the
    # context packet this turn (vision_context, third-party ingested text),
    # the review prompt adds a fifth criterion checking whether the draft
    # attributes subjects/communities/beliefs from that content to the user.
    # False (default) means the criterion is omitted.
    has_third_party_content: bool = False


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