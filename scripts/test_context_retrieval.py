from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(PROJECT_ROOT))

from src.context.ranker import ContextRanker
from src.context.retriever import ContextRetriever


TEST_QUERIES = [
    "What patterns have you noticed lately?",
    "What have I been working on recently?",
    "What is Ember-2 and what have I been building?",
]


@dataclass
class RetrievedSet:
    memory_items: list
    reflection_items: list


def inspect_query(
    user_message: str,
    top_memory: int = 6,
    top_reflections: int = 2,
) -> RetrievedSet:
    retriever = ContextRetriever()
    ranker = ContextRanker()

    memory_items, reflection_items = retriever.retrieve(user_message)
    ranked_memory, ranked_reflections = ranker.rank(memory_items, reflection_items)

    selected_memory = ranked_memory[:top_memory]
    selected_reflections = ranked_reflections[:top_reflections]

    return RetrievedSet(
        memory_items=selected_memory,
        reflection_items=selected_reflections,
    )


def print_items(label: str, items: list) -> None:
    print(f"\n=== {label} ===")
    if not items:
        print("(none)")
        return

    for i, item in enumerate(items, start=1):
        item_type = getattr(item, "item_type", "unknown")
        score = getattr(item, "score", 0.0)
        timestamp = getattr(item, "timestamp", None) or "n/a"
        content = getattr(item, "content", "") or ""
        preview = " ".join(content.split())[:220]

        print(f"\n[{i}] type={item_type} score={score:.4f} timestamp={timestamp}")
        print(preview)


def run_queries(queries: list[str], top_memory: int, top_reflections: int) -> int:
    for query in queries:
        print("\n" + "=" * 80)
        print(f"QUERY: {query}")
        print("=" * 80)

        results = inspect_query(
            user_message=query,
            top_memory=top_memory,
            top_reflections=top_reflections,
        )

        print_items("MEMORY", results.memory_items)
        print_items("REFLECTIONS", results.reflection_items)

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repeatable retrieval smoke test for Ember-2 context selection."
    )
    parser.add_argument(
        "--query",
        action="append",
        help="Custom query to test. Can be provided more than once.",
    )
    parser.add_argument(
        "--top-memory",
        type=int,
        default=6,
        help="How many ranked memory items to print.",
    )
    parser.add_argument(
        "--top-reflections",
        type=int,
        default=2,
        help="How many ranked reflection items to print.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    queries = args.query if args.query else TEST_QUERIES

    print(f"Project root: {PROJECT_ROOT}")

    return run_queries(
        queries=queries,
        top_memory=args.top_memory,
        top_reflections=args.top_reflections,
    )


if __name__ == "__main__":
    raise SystemExit(main())