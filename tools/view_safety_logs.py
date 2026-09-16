from __future__ import annotations

import json
from pathlib import Path


LOG_DIR = Path(__file__).resolve().parents[2] / "logs" / "safety_reviews"


def load_logs(limit: int = 10):
    files = sorted(LOG_DIR.glob("*.json"), reverse=True)
    selected = files[:limit]

    logs = []
    for file in selected:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            logs.append(data)
        except Exception:
            continue

    return logs


def print_logs(limit: int = 10):
    """Print governance summaries for the most recent safety review logs.

    No response or message text is ever printed -- logs written after
    the #151 fix carry only lengths/hashes/session_id, never content
    (see src/safety/review_logger.py). Older logs may still carry
    user_message/draft_response/final_response on disk; this tool does
    not read or display those fields even when present, so it can't
    become a second place that reprints text the log itself should not
    hold.
    """
    logs = load_logs(limit)

    if not logs:
        print("No safety logs found.")
        return

    for log in logs:
        trigger = log.get("trigger")
        review = log.get("review")
        critique = log.get("critique")

        # Backward compatibility for older log format
        if trigger is None:
            safety = log.get("safety", {})
            trigger = {
                "triggered": safety.get("triggered", False),
                "triggered_by": safety.get("triggered_by", []),
                "notes": [],
            }

        if review is None:
            safety = log.get("safety", {})
            review = {
                "triggered": safety.get("triggered", False),
                "outcome": safety.get("outcome", "unknown"),
                "rules": safety.get("rules", []),
            }

        print("=" * 60)
        print(f"Time: {log.get('timestamp', 'unknown')}")
        print(f"Session: {log.get('session_id', 'unknown')}")
        print(f"Triggered: {trigger.get('triggered', False)}")
        print(f"Signals: {trigger.get('triggered_by', [])}")
        print(f"Review Outcome: {review.get('outcome', 'unknown')}")
        print(f"Rules: {review.get('rules', [])}")

        if critique:
            print(f"Severity: {critique.get('severity', 'none')}")
            print(f"Triggered rules: {critique.get('triggered_rules', [])}")
            print(f"Issue count: {critique.get('issue_count', 0)}")

        print(f"User message length: {log.get('user_message_length', 'n/a')}")
        print(f"Draft response length: {log.get('draft_response_length', 'n/a')}")
        print(f"Final response length: {log.get('final_response_length', 'n/a')}")
        print()


if __name__ == "__main__":
    print_logs(limit=10)