from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.context.models import ContextPacket
from src.safety.models import SafetyReviewResult, SafetyTriggerResult


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SafetyReviewLogger:
    """Writes a governance audit trail to logs/safety_reviews/.

    Never writes response or message text (CLAUDE.md Vault Privacy Rule --
    "Ember's generated responses that draw on vault content are also vault
    content"; no exceptions, repo or logs directory). What the log records
    instead, and why it's enough for the log's actual job (proof review
    ran, why it triggered, what it decided -- see docs/NIST_AI_RMF.md):

      - character lengths and SHA-256 hashes of user_message/
        draft_response/final_response. A hash is not the text (one-way,
        non-reversible) and lets exact-repeat detection across entries
        (the repetition-loop diagnostic docs/KNOWN_ISSUES.md B-LOOP-001
        already names as wanted) without ever storing or re-reading
        content.
      - session_id, already available to both callers. The vault, not
        this log, is the system of record for the actual text (CLAUDE.md
        rule 2) -- session_id plus this entry's own timestamp is enough
        for someone with vault access to locate the corresponding
        memory/conversation/ records if the real words are ever needed.
      - trigger/review/metadata sections, which were already
        content-free (SafetyReviewResult.log_payload() only ever
        returned {triggered, outcome, rules}, per ADR-035's context
        allowlist discipline).
      - critique severity/triggered_rules/issue_count, NOT
        issues_found/suggested_changes. issues_found can carry a
        verbatim excerpt of the draft response: review_service.py's
        MVR critique path quotes the failing `sentence` from the
        reviewing LLM's own output into each issue label. That's a
        second, independent text leak beyond user_message/
        draft_response/final_response -- issue_count (a plain len())
        preserves "how many things were flagged" with no content.
    """

    def __init__(self, log_dir: Path | None = None) -> None:
        self.log_dir = log_dir or self._default_log_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        context_packet: ContextPacket,
        draft_response: str,
        trigger_result: SafetyTriggerResult,
        review_result: SafetyReviewResult,
        session_id: str | None = None,
    ) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        file_path = self.log_dir / f"{timestamp}.json"

        user_message = context_packet.user_message
        final_response = self._final_response(review_result, draft_response)

        payload = {
            "timestamp": timestamp,
            "session_id": session_id,
            "user_message_length": len(user_message),
            "user_message_sha256": _sha256(user_message),
            "draft_response_length": len(draft_response),
            "draft_response_sha256": _sha256(draft_response),
            "final_response_length": len(final_response),
            "final_response_sha256": _sha256(final_response),
            "trigger": {
                "triggered": trigger_result.triggered,
                "triggered_by": trigger_result.triggered_by,
                "notes": trigger_result.notes,
            },
            "review": review_result.log_payload(),
            "critique": self._critique_payload(review_result),
            "metadata": self._context_metadata(context_packet),
        }

        file_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return file_path

    def _final_response(
        self,
        review_result: SafetyReviewResult,
        draft_response: str,
    ) -> str:
        if review_result.outcome == "refuse_redirect":
            return review_result.refusal_message or ""
        return review_result.reviewed_text or draft_response

    def _critique_payload(
        self,
        review_result: SafetyReviewResult,
    ) -> dict[str, Any] | None:
        if review_result.critique is None:
            return None

        return {
            "severity": review_result.critique.severity,
            "triggered_rules": review_result.critique.triggered_rules,
            "issue_count": len(review_result.critique.issues_found),
        }

    def _context_metadata(self, context_packet: ContextPacket) -> dict[str, Any]:
        return {
            "memory_count": len(context_packet.memory_items),
            "reflection_count": len(context_packet.reflection_items),
        }

    @staticmethod
    def _default_log_dir() -> Path:
        base_dir = Path(__file__).resolve().parents[2]
        return base_dir / "logs" / "safety_reviews"
