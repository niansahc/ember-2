from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from src.context.service import ContextService


TEST_QUERIES = [
    "What patterns have you noticed lately?",
    "What have I been working on recently?",
    "What is Ember-2 and what have I been building?",
]


def print_items(label: str, items: list) -> None:
    print(f"\n=== {label} ===")
    if not items:
        print("(none)")
        return

    for i, item in enumerate(items, start=1):
        item_type = getattr(item, "item_type", "unknown")
        source = getattr(item, "source", "unknown")
        score = getattr(item, "score", 0.0)
        timestamp = getattr(item, "timestamp", None) or "n/a"
        metadata = getattr(item, "metadata", {}) or {}

        role = metadata.get("role", "n/a")
        content_kind = metadata.get("content_kind", "n/a")
        title = metadata.get("title", "n/a")
        doc_id = metadata.get("doc_id", "n/a")

        content = getattr(item, "content", "") or ""
        preview = " ".join(content.split())[:220]

        print(
            f"\n[{i}] type={item_type} source={source} score={score:.4f} "
            f"timestamp={timestamp}"
        )
        print(
            f"    role={role} content_kind={content_kind} "
            f"title={title} doc_id={doc_id}"
        )
        print(f"    {preview}")


def inspect_query(user_message: str) -> int:
    service = ContextService()
    packet = service.build_context(user_message)

    print("\n" + "=" * 80)
    print(f"QUERY: {user_message}")
    print("=" * 80)

    print_items("FINAL MEMORY", packet.memory_items)
    print_items("FINAL REFLECTIONS", packet.reflection_items)

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repeatable end-to-end context assembly smoke test for Ember-2."
    )
    parser.add_argument(
        "--query",
        action="append",
        help="Custom query to test. Can be provided more than once.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queries = args.query if args.query else TEST_QUERIES

    print(f"Project root: {PROJECT_ROOT}")

    for query in queries:
        inspect_query(query)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())