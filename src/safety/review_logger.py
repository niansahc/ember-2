from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.context.models import ContextPacket
from src.safety.models import SafetyReviewResult, SafetyTriggerResult


class SafetyReviewLogger:
    def __init__(self, log_dir: Path | None = None) -> None:
        self.log_dir = log_dir or self._default_log_dir()
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(
        self,
        context_packet: ContextPacket,
        draft_response: str,
        trigger_result: SafetyTriggerResult,
        review_result: SafetyReviewResult,
    ) -> Path:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        file_path = self.log_dir / f"{timestamp}.json"

        payload = {
            "timestamp": timestamp,
            "user_message": context_packet.user_message,
            "draft_response": draft_response,
            "final_response": self._final_response(review_result, draft_response),
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
            "issues_found": review_result.critique.issues_found,
            "severity": review_result.critique.severity,
            "suggested_changes": review_result.critique.suggested_changes,
            "triggered_rules": review_result.critique.triggered_rules,
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