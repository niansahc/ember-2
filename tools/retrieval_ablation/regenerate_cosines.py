"""
tools/retrieval_ablation/regenerate_cosines.py

Re-measures MEASURED_COSINES in corpus.py against the live embedding model.

Run this after ANY edit to a fixture text or a stratum query:

    python -m tools.retrieval_ablation.regenerate_cosines

The corpus freezes real nomic-embed-text similarities rather than stipulating
them, because a hand-assigned cosine tends to agree with the hand-assigned
grade and that agreement silently hands the naive baseline the answer key --
which is exactly what happened on the first build of this eval. Freezing keeps
the run offline and deterministic; this script is how the frozen values stay
honest.

Edits the MEASURED_COSINES block and CORPUS_TEXT_DIGEST in place. Needs the
embedding model reachable (Ollama), unlike the eval itself.
"""

from __future__ import annotations

import math
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CORPUS_PATH = Path(__file__).resolve().parent / "corpus.py"


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb)


def main() -> int:
    from src.retrieval.embedding_model import embed_text

    from .corpus import FIXTURES, STRATA, corpus_text_digest

    cache: dict[str, list[float]] = {}

    def vec(text: str) -> list[float]:
        if text not in cache:
            cache[text] = embed_text(text)
        return cache[text]

    rows: list[str] = ["MEASURED_COSINES: dict[str, dict[str, float]] = {"]
    for stratum in STRATA:
        query_vec = vec(stratum.query)
        rows.append(f'    "{stratum.name}": {{')
        for fixture in FIXTURES:
            value = _cosine(query_vec, vec(fixture.text))
            rows.append(f'        "{fixture.id}": {value:.3f},')
        rows.append("    },")
    rows.append("}")

    source = CORPUS_PATH.read_text(encoding="utf-8")

    pattern = re.compile(
        r"MEASURED_COSINES: dict\[str, dict\[str, float\]\] = \{.*?\n\}", re.DOTALL
    )
    if not pattern.search(source):
        print("Could not find the MEASURED_COSINES block in corpus.py", file=sys.stderr)
        return 1
    source = pattern.sub("\n".join(rows), source, count=1)

    digest = corpus_text_digest()
    source = re.sub(
        r'CORPUS_TEXT_DIGEST = "[0-9a-f]*"',
        f'CORPUS_TEXT_DIGEST = "{digest}"',
        source,
        count=1,
    )

    CORPUS_PATH.write_text(source, encoding="utf-8")
    print(f"Rewrote {len(STRATA)} x {len(FIXTURES)} cosines; digest {digest[:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
