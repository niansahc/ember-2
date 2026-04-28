"""
scripts/extract_classifier_labels.py

ADR-037 Step A scaffolding — promotion pipeline for the multi-class
intent classifier.

After Step B ships (post-v0.17.1), every user query causes
intent_classifier.classify_intent() to emit one structured log line:

    [INTENT_CLASSIFY] stage=<stage> label=<label> confidence=<float> query=<text>

This script walks a directory of log files (typically uvicorn captured
output), extracts those lines, deduplicates by query string, groups by
label, and writes a TSV the manager can review. High-confidence Stage 2
entries are candidates to promote into src/llm/classifier_examples.py
(after the privacy review described in CLAUDE.md's Vault Privacy Rule).

Step A ships the extractor but does NOT execute it as part of the
release flow. The output file is gitignored so a careless run cannot
commit vault content.

Usage
-----
    python scripts/extract_classifier_labels.py \
        --input logs/uvicorn-stdout.log \
        --output extract_classifier_labels_output/candidates.tsv \
        --limit-per-label 30 \
        --min-confidence 0.65

Privacy posture
---------------
The script only reads files the user explicitly passes via --input.
It never reaches into private_vault/. It treats query text as
potentially-sensitive: the output file lives under a gitignored
directory, and the script prints a clear privacy reminder on each run.

A human MUST review the TSV before promoting any row into
classifier_examples.py — that file is committed to git, so verbatim
user queries (which may contain proper names, locations, etc.) must be
paraphrased to generic patterns first.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

# Match the structured log line emitted by intent_classifier._log().
# Tolerant of leading uvicorn timestamp / log-level prefixes — anchors
# on the [INTENT_CLASSIFY] tag rather than the line start.
_LOG_LINE_RE = re.compile(
    r"\[INTENT_CLASSIFY\]\s+"
    r"stage=(?P<stage>\S+)\s+"
    r"label=(?P<label>\S+)\s+"
    r"confidence=(?P<confidence>\S+)\s+"
    r"query=(?P<query>.*?)\s*$"
)


@dataclass(frozen=True)
class Candidate:
    query: str
    label: str
    stage: str
    confidence: float | None


def parse_log_line(line: str) -> Candidate | None:
    """Return a Candidate for a single log line, or None if it doesn't match."""
    match = _LOG_LINE_RE.search(line)
    if not match:
        return None

    raw_conf = match.group("confidence")
    if raw_conf == "none":
        confidence: float | None = None
    else:
        try:
            confidence = float(raw_conf)
        except ValueError:
            confidence = None

    return Candidate(
        query=match.group("query").strip(),
        label=match.group("label"),
        stage=match.group("stage"),
        confidence=confidence,
    )


def extract(
    input_paths: list[Path], min_confidence: float | None
) -> list[Candidate]:
    """Walk input files, emit deduplicated Candidates."""
    seen: set[str] = set()
    candidates: list[Candidate] = []

    for path in input_paths:
        if not path.exists():
            print(f"  [SKIP] {path} (does not exist)", file=sys.stderr)
            continue
        try:
            with path.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    cand = parse_log_line(line)
                    if cand is None:
                        continue
                    if cand.query in seen:
                        continue
                    if (
                        min_confidence is not None
                        and cand.confidence is not None
                        and cand.confidence < min_confidence
                    ):
                        continue
                    seen.add(cand.query)
                    candidates.append(cand)
        except OSError as exc:
            print(f"  [ERROR] {path}: {exc}", file=sys.stderr)

    return candidates


def sample_per_label(
    candidates: list[Candidate], limit_per_label: int
) -> list[Candidate]:
    """Group by label, take top-confidence N per group, flatten."""
    by_label: dict[str, list[Candidate]] = defaultdict(list)
    for cand in candidates:
        by_label[cand.label].append(cand)

    sampled: list[Candidate] = []
    for label in sorted(by_label):
        rows = by_label[label]
        rows.sort(
            key=lambda c: (c.confidence if c.confidence is not None else -1.0),
            reverse=True,
        )
        sampled.extend(rows[:limit_per_label])
    return sampled


def write_tsv(candidates: list[Candidate], output: Path) -> None:
    """Write TSV to output, creating parent directory if missing."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        fh.write("query\tlabel\tstage\tconfidence\n")
        for cand in candidates:
            conf = "" if cand.confidence is None else f"{cand.confidence:.4f}"
            # Tabs and newlines in the query would corrupt the TSV; queries
            # with these are dropped to avoid silent format breakage.
            if "\t" in cand.query or "\n" in cand.query:
                continue
            fh.write(f"{cand.query}\t{cand.label}\t{cand.stage}\t{conf}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract [INTENT_CLASSIFY] log lines into a reviewable TSV. "
            "Output lives under a gitignored directory and must be "
            "human-reviewed before any row is promoted into "
            "src/llm/classifier_examples.py."
        )
    )
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        type=Path,
        help="Log file to scan. May be passed multiple times.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("extract_classifier_labels_output/candidates.tsv"),
        help="Output TSV path. Default: extract_classifier_labels_output/candidates.tsv",
    )
    parser.add_argument(
        "--limit-per-label",
        type=int,
        default=30,
        help="Maximum candidates emitted per label, sorted by confidence desc.",
    )
    parser.add_argument(
        "--min-confidence",
        type=float,
        default=None,
        help=(
            "If set, drop candidates whose confidence is present and below "
            "this threshold. Stage 1 / stage 3 timeout entries (confidence=none) "
            "are kept regardless."
        ),
    )
    args = parser.parse_args(argv)

    print(
        "Privacy reminder: queries extracted here may contain personal content. "
        "Output is gitignored. Review every row before promoting into "
        "classifier_examples.py — paraphrase to generic patterns.",
        file=sys.stderr,
    )

    candidates = extract(args.input, args.min_confidence)
    sampled = sample_per_label(candidates, args.limit_per_label)
    write_tsv(sampled, args.output)

    by_label_counts: dict[str, int] = defaultdict(int)
    for c in sampled:
        by_label_counts[c.label] += 1

    print(
        f"Wrote {len(sampled)} candidates across {len(by_label_counts)} labels to {args.output}",
        file=sys.stderr,
    )
    for label in sorted(by_label_counts):
        print(f"  {label}: {by_label_counts[label]}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
