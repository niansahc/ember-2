from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from src.retrieval.semantic_search import semantic_search


def run_test(query):
    print(f"\n--- Query: {query} ---\n")

    results = semantic_search(query, limit=5)

    if not results:
        print("No results found")
        return

    for i, r in enumerate(results, 1):
        print(f"\nResult {i}")
        print(f"Score: {r.get('score')}")
        print(f"Path: {r.get('path')}")
        print(f"Content Preview:\n{r.get('content')[:300]}")
        print("-" * 40)


if __name__ == "__main__":
    run_test("trigeminal neuralgia")
    run_test("Ozempic")
    