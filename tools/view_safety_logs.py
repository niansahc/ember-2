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
        print(f"User: {log.get('user_message', '')}")
        print(f"Triggered: {trigger.get('triggered', False)}")
        print(f"Signals: {trigger.get('triggered_by', [])}")
        print(f"Review Outcome: {review.get('outcome', 'unknown')}")
        print(f"Rules: {review.get('rules', [])}")

        if critique:
            print(f"Severity: {critique.get('severity', 'none')}")
            print(f"Issues: {critique.get('issues_found', [])}")

        print("\nDraft Response:")
        print(log.get("draft_response", ""))

        print("\nFinal Response:")
        print(log.get("final_response", ""))
        print()


if __name__ == "__main__":
    print_logs(limit=10)