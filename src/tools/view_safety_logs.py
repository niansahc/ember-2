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
        print("=" * 60)
        print(f"Time: {log['timestamp']}")
        print(f"User: {log['user_message']}")
        print(f"Triggered: {log['trigger']['triggered']}")
        print(f"Signals: {log['trigger'].get('triggered_by', [])}")
        print(f"Review Outcome: {log['review']['outcome']}")

        if log.get("critique"):
            print(f"Severity: {log['critique']['severity']}")
            print(f"Issues: {log['critique']['issues_found']}")

        print("\nDraft Response:")
        print(log["draft_response"])
        print("\nFinal Response:")
        print(log["final_response"])
        print()


if __name__ == "__main__":
    print_logs(limit=10)