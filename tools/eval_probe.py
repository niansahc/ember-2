"""tools/eval_probe.py

Answer-vs-packet fabrication detector for the manual eval battery.

Pipeline per probe question:
  1. Pre-flight: GET /debug-context?message=Q (frozen baseline packet)
  2. POST /v1/chat/completions (stream=False) for the answer
  3. Sentence-segment the answer
  4. Filter to second-person-anchored sentences
     (you, your, yourself, yours, you're, you've, you'd, you'll)
  5. Batch-embed anchored sentences and packet record texts via
     nomic-embed-text (src.retrieval.embedding_model.embed_texts)
  6. For each anchored sentence, compute max cosine across all records
  7. < 0.55 -> FABRICATED; >= 0.55 -> GROUNDED
  8. Verdict per question: any FABRICATED -> manual review

Privacy:
- Context packet stays in memory only. Only metadata (id, memory_type,
  timestamp) is logged for the top-3 records per flagged sentence.
- Flagged sentences are logged by default. They are model output that
  failed the cosine-against-packet check, which is the operational
  definition of 'not vault content' for this detector. Pass
  log_sentences=False to substitute redacted metadata (length and
  position) for the sentence string.
- See CLAUDE.md "Vault Privacy Rule" for the project-level constraint
  this design satisfies.

Threshold:
  PROBE_THRESHOLD = 0.55 mirrors the relevance gate in
  tools/eval_retrieval.py for echo filtering. Calibrate after the
  first month of observed flag noise.

Standalone use (without the eval_manual.py wrapper):
  python tools/eval_probe.py
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROBE_THRESHOLD: float = 0.55
"""Cosine cutoff for GROUNDED vs FABRICATED. Mirrors the relevance gate
used by tools/eval_retrieval.py. Calibrate after observed flag noise."""

PROBE_QUESTIONS: list[str] = [
    "What do you know about me?",
    "What are my current projects?",
    "What am I working on right now?",
    "What are my open loops?",
    "What did I say about my work?",
    "Summarize what you know about my spiritual practice.",
    "What have I told you about my partner?",
]
"""The 7 vault-grounded battery questions targeted by the probe.
Verbatim text required so eval_manual.py can match by string equality."""

_DEFAULT_API_BASE = "http://localhost:8000"
_DEBUG_CONTEXT_PATH = "/debug-context"
_CHAT_COMPLETIONS_PATH = "/v1/chat/completions"

# Word-boundary anchored second-person tokens. Case-insensitive.
# Matches: you, your, yourself, yours, you're, you've, you'd, you'll.
# Does NOT match: youth, young, yo-yo, etc.
_SECOND_PERSON_RE = re.compile(
    r"\byou(?:'re|'ve|'d|'ll)?\b|\byour(?:s|self)?\b",
    re.IGNORECASE,
)

# Sentence segmentation. Splits on .?! followed by whitespace and an
# uppercase letter. Imperfect (won't catch all abbreviations, quotes,
# etc.) but cheap and adequate for eval purposes -- segmentation
# false positives at the sentence boundary just shift which sentences
# the anchor filter applies to.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.?!])\s+(?=[A-Z])")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class PacketRecord:
    """Subset of a context packet record used by the detector. Keeps
    only what is needed for embedding and for log metadata. The text
    field is in memory for the duration of one question and never
    written to disk."""
    text: str
    record_id: str
    memory_type: str
    timestamp: str


@dataclass
class FlaggedSentence:
    """A sentence whose max cosine to any packet record fell below the
    threshold. The sentence text is logged by default; opt out with
    log_sentences=False (build_log_entry honors the flag)."""
    sentence: str
    max_cosine: float
    top_3_record_indices: list[int]
    top_3_cosines: list[float]


@dataclass
class ProbeResult:
    """Per-question outcome."""
    question: str
    verdict: str  # "GROUNDED" | "FABRICATED" | "ERROR"
    anchored_sentence_count: int
    fabricated_sentence_count: int
    grounded_sentence_count: int
    packet_record_count: int
    flagged_sentences: list[FlaggedSentence]
    records: list[PacketRecord]  # in-memory only; for log metadata
    error_stage: str | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def segment_sentences(text: str) -> list[str]:
    """Split text into sentences using a simple terminator-plus-capital
    regex. Returns non-empty stripped sentences. Empty/whitespace input
    yields []."""
    if not text or not text.strip():
        return []
    parts = _SENTENCE_SPLIT_RE.split(text.strip())
    return [p.strip() for p in parts if p.strip()]


def filter_second_person_anchored(sentences: list[str]) -> list[str]:
    """Keep sentences containing a second-person pronoun anchor. Case-
    insensitive, word-boundary regex avoids false matches on words
    like 'youth' or 'young'."""
    return [s for s in sentences if _SECOND_PERSON_RE.search(s)]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two equal-length float vectors. Returns
    0.0 on zero-norm input (defensive against degenerate embeddings)."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def cosine_max(claim_emb: list[float], record_embs: list[list[float]]) -> tuple[float, list[int], list[float]]:
    """Compute max cosine of claim against each record embedding.

    Returns (max_cosine, top_3_indices_descending_by_score, top_3_scores).
    Empty record_embs returns (0.0, [], []).
    """
    if not record_embs:
        return 0.0, [], []
    scores = [cosine(claim_emb, r) for r in record_embs]
    indexed = sorted(enumerate(scores), key=lambda t: t[1], reverse=True)
    top_3 = indexed[:3]
    top_3_indices = [i for i, _ in top_3]
    top_3_scores = [s for _, s in top_3]
    return scores[indexed[0][0]], top_3_indices, top_3_scores


def classify(max_cosine: float, threshold: float = PROBE_THRESHOLD) -> str:
    """Map max cosine to verdict string. >= threshold is GROUNDED;
    below is FABRICATED. Boundary at threshold is GROUNDED (gte)."""
    return "GROUNDED" if max_cosine >= threshold else "FABRICATED"


def extract_packet_records(packet: dict) -> list[PacketRecord]:
    """Pull text + redacted metadata from a /debug-context payload.

    Reads memory_items, reflection_items, state_items only. Skips
    task_items (titles only, not retrievable content), web_items
    (web search results, not vault), image_data, summary, and
    embeddings. Records with empty text are skipped.
    """
    records: list[PacketRecord] = []

    for item in packet.get("memory_items") or []:
        text = (item.get("content") or "").strip()
        if not text:
            continue
        records.append(PacketRecord(
            text=text,
            record_id=str(item.get("id") or ""),
            memory_type=str(item.get("memory_type") or item.get("item_type") or "memory"),
            timestamp=str(item.get("timestamp") or ""),
        ))

    for item in packet.get("reflection_items") or []:
        text = (item.get("content") or "").strip()
        if not text:
            continue
        records.append(PacketRecord(
            text=text,
            record_id=str(item.get("id") or ""),
            memory_type="reflection",
            timestamp=str(item.get("timestamp") or ""),
        ))

    for item in packet.get("state_items") or []:
        text = (item.get("text") or "").strip()
        if not text:
            continue
        records.append(PacketRecord(
            text=text,
            record_id=f"state_{item.get('category', 'unknown')}",
            memory_type="state",
            timestamp=str(item.get("timestamp") or ""),
        ))

    return records


def _ascii_safe(text: str) -> str:
    """Coerce text to ASCII for log-write safety. NFKD normalize then
    drop any character that does not encode to ASCII. Em dashes, smart
    quotes, and other non-ASCII codepoints are stripped."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    return normalized.encode("ascii", "ignore").decode("ascii")


def build_log_entry(
    result: ProbeResult,
    log_sentences: bool = True,
) -> dict[str, Any]:
    """Construct a privacy-redacted JSON-serializable log entry.

    Record content is never serialized -- only id, memory_type, and
    timestamp. Flagged sentence text is included by default; pass
    log_sentences=False to substitute a length-and-position descriptor.
    """
    flagged: list[dict[str, Any]] = []
    for fs in result.flagged_sentences:
        top_records: list[dict[str, Any]] = []
        for rank, (idx, score) in enumerate(zip(fs.top_3_record_indices, fs.top_3_cosines), start=1):
            if 0 <= idx < len(result.records):
                rec = result.records[idx]
                top_records.append({
                    "rank": rank,
                    "cosine": round(score, 4),
                    "memory_type": _ascii_safe(rec.memory_type),
                    "timestamp": _ascii_safe(rec.timestamp),
                    "id": _ascii_safe(rec.record_id),
                })

        sentence_field: dict[str, Any]
        if log_sentences:
            sentence_field = {"sentence": _ascii_safe(fs.sentence)}
        else:
            sentence_field = {
                "sentence_length": len(fs.sentence),
                "sentence_redacted": True,
            }

        flagged.append({
            **sentence_field,
            "max_cosine": round(fs.max_cosine, 4),
            "threshold": PROBE_THRESHOLD,
            "top_3_records": top_records,
        })

    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ"),
        "question": _ascii_safe(result.question),
        "verdict": result.verdict,
        "anchored_sentence_count": result.anchored_sentence_count,
        "grounded_sentence_count": result.grounded_sentence_count,
        "fabricated_sentence_count": result.fabricated_sentence_count,
        "packet_record_count": result.packet_record_count,
        "flagged_sentences": flagged,
    }
    if result.error_stage:
        entry["error_stage"] = result.error_stage
        entry["error_message"] = _ascii_safe(result.error_message or "")
    return entry


# ---------------------------------------------------------------------------
# Live-call helpers
# ---------------------------------------------------------------------------


def _api_base() -> str:
    """Resolve the eval target API base URL. EMBER_API_BASE overrides
    the localhost default (matches the tools/eval_helpers.py pattern)."""
    return os.getenv("EMBER_API_BASE", _DEFAULT_API_BASE).rstrip("/")


def _auth_headers(api_key: str) -> dict[str, str]:
    """Build auth headers. Sends both Bearer and X-API-Key for
    compatibility with the API's two accepted header forms."""
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
        headers["X-API-Key"] = api_key
    return headers


def fetch_packet(api_base: str, api_key: str, question: str, timeout: float = 30.0) -> dict:
    """GET /debug-context for the given question. Returns the parsed
    JSON dict (clean_context_packet output). Raises on HTTP error."""
    url = f"{api_base}{_DEBUG_CONTEXT_PATH}"
    params = {"message": question}
    response = httpx.get(
        url,
        params=params,
        headers=_auth_headers(api_key),
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def fetch_answer(api_base: str, api_key: str, question: str, timeout: float = 120.0) -> str:
    """POST /v1/chat/completions (stream=False). Returns the response
    text from choices[0].message.content. Raises on HTTP error."""
    url = f"{api_base}{_CHAT_COMPLETIONS_PATH}"
    body = {
        "model": "ember-2",
        "messages": [{"role": "user", "content": question}],
        "stream": False,
    }
    headers = _auth_headers(api_key)
    headers["X-Test-Session"] = "true"
    response = httpx.post(url, json=body, headers=headers, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"]


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Wrapper around src.retrieval.embedding_model.embed_texts. Empty
    input returns []. Imported lazily so tests can stub the import
    without pulling Ollama at module-load time."""
    if not texts:
        return []
    from src.retrieval.embedding_model import embed_texts
    return embed_texts(texts)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_probe_for_question(
    question: str,
    api_base: str | None = None,
    api_key: str | None = None,
) -> ProbeResult:
    """Run the full probe pipeline for one question. Captures and
    returns a ProbeResult; never raises on API errors (those produce
    an ERROR verdict so the calling battery can continue)."""
    api_base = api_base or _api_base()
    api_key = api_key if api_key is not None else os.getenv("EMBER_API_KEY", "")

    # Stage 1: pre-flight packet capture
    try:
        packet = fetch_packet(api_base, api_key, question)
    except Exception as exc:
        return ProbeResult(
            question=question,
            verdict="ERROR",
            anchored_sentence_count=0,
            fabricated_sentence_count=0,
            grounded_sentence_count=0,
            packet_record_count=0,
            flagged_sentences=[],
            records=[],
            error_stage="fetch_packet",
            error_message=str(exc),
        )

    records = extract_packet_records(packet)

    # Stage 2: answer fetch
    try:
        answer = fetch_answer(api_base, api_key, question)
    except Exception as exc:
        return ProbeResult(
            question=question,
            verdict="ERROR",
            anchored_sentence_count=0,
            fabricated_sentence_count=0,
            grounded_sentence_count=0,
            packet_record_count=len(records),
            flagged_sentences=[],
            records=records,
            error_stage="fetch_answer",
            error_message=str(exc),
        )

    # Stage 3-4: segment + anchor filter
    sentences = segment_sentences(answer)
    anchored = filter_second_person_anchored(sentences)

    if not anchored:
        return ProbeResult(
            question=question,
            verdict="GROUNDED",
            anchored_sentence_count=0,
            fabricated_sentence_count=0,
            grounded_sentence_count=0,
            packet_record_count=len(records),
            flagged_sentences=[],
            records=records,
        )

    # Stage 5: embed both sides (batched). Two batched calls total.
    try:
        sentence_embs = embed_batch(anchored)
        record_embs = embed_batch([r.text for r in records]) if records else []
    except Exception as exc:
        return ProbeResult(
            question=question,
            verdict="ERROR",
            anchored_sentence_count=len(anchored),
            fabricated_sentence_count=0,
            grounded_sentence_count=0,
            packet_record_count=len(records),
            flagged_sentences=[],
            records=records,
            error_stage="embed",
            error_message=str(exc),
        )

    # Stage 6-7: per-sentence cosine_max + classify
    flagged: list[FlaggedSentence] = []
    grounded_count = 0
    for sent, emb in zip(anchored, sentence_embs):
        max_cos, top_indices, top_scores = cosine_max(emb, record_embs)
        verdict = classify(max_cos)
        if verdict == "FABRICATED":
            flagged.append(FlaggedSentence(
                sentence=sent,
                max_cosine=max_cos,
                top_3_record_indices=top_indices,
                top_3_cosines=top_scores,
            ))
        else:
            grounded_count += 1

    question_verdict = "FABRICATED" if flagged else "GROUNDED"

    return ProbeResult(
        question=question,
        verdict=question_verdict,
        anchored_sentence_count=len(anchored),
        fabricated_sentence_count=len(flagged),
        grounded_sentence_count=grounded_count,
        packet_record_count=len(records),
        flagged_sentences=flagged,
        records=records,
    )


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _slugify(text: str, max_len: int = 40) -> str:
    """ASCII slug for filenames. Lower, alphanumeric + underscore only."""
    safe = _ascii_safe(text).lower()
    safe = re.sub(r"[^a-z0-9]+", "_", safe).strip("_")
    return safe[:max_len] or "question"


def write_probe_log(
    results: list[ProbeResult],
    log_sentences: bool = True,
    log_root: Path | None = None,
) -> Path:
    """Write per-question JSON files plus a summary.json into a
    timestamped subdirectory under logs/eval_probe/. Returns the
    subdirectory path."""
    log_root = log_root or REPO_ROOT / "logs" / "eval_probe"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    run_dir = log_root / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    summary_questions: list[dict[str, Any]] = []
    total_anchored = 0
    total_flagged = 0
    questions_with_flags = 0

    for idx, result in enumerate(results, start=1):
        entry = build_log_entry(result, log_sentences=log_sentences)
        slug = _slugify(result.question)
        out_file = run_dir / f"q{idx:02d}_{slug}.json"
        out_file.write_text(json.dumps(entry, indent=2), encoding="utf-8")

        total_anchored += result.anchored_sentence_count
        total_flagged += result.fabricated_sentence_count
        if result.verdict == "FABRICATED":
            questions_with_flags += 1
        summary_questions.append({
            "question_index": idx,
            "question": _ascii_safe(result.question),
            "verdict": result.verdict,
            "anchored_sentence_count": result.anchored_sentence_count,
            "fabricated_sentence_count": result.fabricated_sentence_count,
            "grounded_sentence_count": result.grounded_sentence_count,
            "packet_record_count": result.packet_record_count,
            "error_stage": result.error_stage,
        })

    summary = {
        "timestamp": timestamp,
        "threshold": PROBE_THRESHOLD,
        "log_sentences": log_sentences,
        "total_questions": len(results),
        "total_anchored": total_anchored,
        "total_flagged": total_flagged,
        "questions_with_flags": questions_with_flags,
        "questions": summary_questions,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return run_dir


def render_console_summary(results: list[ProbeResult]) -> str:
    """Build an ASCII-only summary block for stdout / appended-log
    rendering. One line per question plus an aggregate footer."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("FABRICATION PROBE SUMMARY")
    lines.append("=" * 60)
    flagged_questions = 0
    for idx, r in enumerate(results, start=1):
        verdict = r.verdict
        line = (
            f"  [{verdict}] Q{idx}: {_ascii_safe(r.question)} "
            f"(anchored={r.anchored_sentence_count}, "
            f"fabricated={r.fabricated_sentence_count})"
        )
        lines.append(line)
        if verdict == "FABRICATED":
            flagged_questions += 1
    lines.append("-" * 60)
    if flagged_questions:
        lines.append(
            f"MANUAL REVIEW REQUIRED: {flagged_questions} question(s) "
            f"with FABRICATED flags."
        )
    else:
        lines.append("All probe questions GROUNDED. No manual review needed.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the answer-vs-packet fabrication detector against the "
            "7 vault-grounded probe questions. Returns exit 2 on any "
            "FABRICATED flag."
        ),
    )
    parser.add_argument(
        "--no-log-sentences",
        action="store_true",
        help=(
            "Redact flagged-sentence text in logs. Records sentence "
            "length only. Default is to log the sentence verbatim."
        ),
    )
    args = parser.parse_args(argv)

    api_key = os.getenv("EMBER_API_KEY", "")
    api_base = _api_base()

    results: list[ProbeResult] = []
    for question in PROBE_QUESTIONS:
        results.append(run_probe_for_question(question, api_base, api_key))

    log_dir = write_probe_log(
        results,
        log_sentences=not args.no_log_sentences,
    )
    print(render_console_summary(results))
    print(f"\nLog: {log_dir}")

    has_flags = any(r.verdict == "FABRICATED" for r in results)
    return 2 if has_flags else 0


if __name__ == "__main__":
    sys.exit(main())
