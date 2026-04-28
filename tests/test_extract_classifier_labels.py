"""
tests/test_extract_classifier_labels.py

ADR-037 Step A — verify the [INTENT_CLASSIFY] log-line parser in
scripts/extract_classifier_labels.py handles the full structured-log
format emitted by intent_classifier._log() and the failure modes that
matter (Stage 3 timeout with confidence=none, malformed lines,
duplicate queries across files).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "extract_classifier_labels.py"
)


def _load_module():
    """Load extract_classifier_labels.py as a module despite living in scripts/.

    scripts/ is not a package, so an ordinary import will not find it. We
    resolve the file path explicitly and execute it under a stable module
    name so the load cost is paid once across the test session.
    """
    name = "extract_classifier_labels_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def extractor():
    return _load_module()


# ---------------------------------------------------------------------------
# parse_log_line — the core normalizer
# ---------------------------------------------------------------------------


def test_parse_log_line_stage1_with_no_confidence(extractor) -> None:
    line = (
        "INFO:ember.intent_classifier:[INTENT_CLASSIFY] stage=stage1 "
        "label=vault_answerable confidence=none query=thank you"
    )
    result = extractor.parse_log_line(line)
    assert result is not None
    assert result.stage == "stage1"
    assert result.label == "vault_answerable"
    assert result.confidence is None
    assert result.query == "thank you"


def test_parse_log_line_stage2_with_confidence(extractor) -> None:
    line = (
        "[INTENT_CLASSIFY] stage=stage2 label=needs_internet "
        "confidence=0.812 query=current price of bitcoin"
    )
    result = extractor.parse_log_line(line)
    assert result is not None
    assert result.stage == "stage2"
    assert result.label == "needs_internet"
    assert result.confidence == pytest.approx(0.812)
    assert result.query == "current price of bitcoin"


def test_parse_log_line_stage3_timeout(extractor) -> None:
    """Stage 3 timeout entries log with confidence=none — they remain
    promotion candidates for the safe-default bucket."""
    line = (
        "[INTENT_CLASSIFY] stage=timeout label=vault_answerable "
        "confidence=none query=hello what is up"
    )
    result = extractor.parse_log_line(line)
    assert result is not None
    assert result.stage == "timeout"
    assert result.confidence is None


def test_parse_log_line_with_uvicorn_timestamp_prefix(extractor) -> None:
    """Real uvicorn logs prefix every line with timestamp/level — the
    regex must anchor on the [INTENT_CLASSIFY] tag, not start of line."""
    line = (
        "2026-04-26 14:32:11,448 INFO ember.intent_classifier "
        "[INTENT_CLASSIFY] stage=stage1 label=needs_internet "
        "confidence=none query=weather today"
    )
    result = extractor.parse_log_line(line)
    assert result is not None
    assert result.query == "weather today"


def test_parse_log_line_returns_none_for_unrelated_line(extractor) -> None:
    assert extractor.parse_log_line("INFO: server started") is None
    assert extractor.parse_log_line("") is None
    assert extractor.parse_log_line("ERROR: ollama unreachable") is None


def test_parse_log_line_returns_none_for_malformed_intent_line(extractor) -> None:
    """Missing required fields should not crash — return None and skip."""
    result = extractor.parse_log_line("[INTENT_CLASSIFY] just garbage")
    assert result is None


def test_parse_log_line_handles_invalid_confidence_gracefully(extractor) -> None:
    """If a future log format changes confidence to e.g. 'NaN' or '?' the
    parser should keep the row but mark confidence as None rather than
    crashing the entire extraction run."""
    line = (
        "[INTENT_CLASSIFY] stage=stage2 label=needs_internet "
        "confidence=garbage query=test"
    )
    result = extractor.parse_log_line(line)
    assert result is not None
    assert result.confidence is None


# ---------------------------------------------------------------------------
# extract — file walking + dedup + min-confidence filtering
# ---------------------------------------------------------------------------


def test_extract_dedups_by_query_across_files(extractor, tmp_path: Path) -> None:
    """Same query appearing in two files (e.g. across uvicorn rotations)
    should produce only one Candidate row."""
    log1 = tmp_path / "log1.txt"
    log2 = tmp_path / "log2.txt"
    log1.write_text(
        "[INTENT_CLASSIFY] stage=stage2 label=needs_internet "
        "confidence=0.85 query=current price of bitcoin\n",
        encoding="utf-8",
    )
    log2.write_text(
        "[INTENT_CLASSIFY] stage=stage2 label=needs_internet "
        "confidence=0.91 query=current price of bitcoin\n",
        encoding="utf-8",
    )
    candidates = extractor.extract([log1, log2], min_confidence=None)
    assert len(candidates) == 1
    # First-seen wins — file order matters.
    assert candidates[0].confidence == pytest.approx(0.85)


def test_extract_skips_missing_files_without_crashing(
    extractor, tmp_path: Path
) -> None:
    real_log = tmp_path / "real.txt"
    real_log.write_text(
        "[INTENT_CLASSIFY] stage=stage1 label=vault_answerable "
        "confidence=none query=thanks\n",
        encoding="utf-8",
    )
    missing = tmp_path / "does_not_exist.txt"
    candidates = extractor.extract([missing, real_log], min_confidence=None)
    assert len(candidates) == 1
    assert candidates[0].query == "thanks"


def test_extract_min_confidence_drops_low_score_rows(
    extractor, tmp_path: Path
) -> None:
    log = tmp_path / "log.txt"
    log.write_text(
        "[INTENT_CLASSIFY] stage=stage2 label=needs_internet confidence=0.40 query=low\n"
        "[INTENT_CLASSIFY] stage=stage2 label=needs_internet confidence=0.80 query=high\n",
        encoding="utf-8",
    )
    candidates = extractor.extract([log], min_confidence=0.65)
    assert {c.query for c in candidates} == {"high"}


def test_extract_min_confidence_keeps_none_confidence_rows(
    extractor, tmp_path: Path
) -> None:
    """Stage 1 / Stage 3 timeout rows have confidence=none; they should
    survive the min-confidence filter regardless of threshold."""
    log = tmp_path / "log.txt"
    log.write_text(
        "[INTENT_CLASSIFY] stage=stage1 label=vault_answerable "
        "confidence=none query=ok\n",
        encoding="utf-8",
    )
    candidates = extractor.extract([log], min_confidence=0.95)
    assert len(candidates) == 1
    assert candidates[0].query == "ok"


# ---------------------------------------------------------------------------
# sample_per_label — top-N sorted by confidence
# ---------------------------------------------------------------------------


def test_sample_per_label_returns_top_n_by_confidence(extractor) -> None:
    Candidate = extractor.Candidate
    rows = [
        Candidate(query="a", label="x", stage="stage2", confidence=0.5),
        Candidate(query="b", label="x", stage="stage2", confidence=0.9),
        Candidate(query="c", label="x", stage="stage2", confidence=0.7),
        Candidate(query="d", label="y", stage="stage2", confidence=0.6),
    ]
    sampled = extractor.sample_per_label(rows, limit_per_label=2)
    sampled_x = [r for r in sampled if r.label == "x"]
    sampled_y = [r for r in sampled if r.label == "y"]
    # Top-2 of x ranked by confidence
    assert [r.query for r in sampled_x] == ["b", "c"]
    # y has only one row; full bucket included
    assert [r.query for r in sampled_y] == ["d"]


# ---------------------------------------------------------------------------
# write_tsv — output integrity
# ---------------------------------------------------------------------------


def test_write_tsv_writes_header_and_rows(extractor, tmp_path: Path) -> None:
    Candidate = extractor.Candidate
    rows = [
        Candidate(query="hello", label="vault_answerable", stage="stage1", confidence=None),
        Candidate(query="bitcoin price", label="needs_internet", stage="stage2", confidence=0.91),
    ]
    output = tmp_path / "nested" / "candidates.tsv"
    extractor.write_tsv(rows, output)
    contents = output.read_text(encoding="utf-8").splitlines()
    assert contents[0] == "query\tlabel\tstage\tconfidence"
    assert contents[1] == "hello\tvault_answerable\tstage1\t"
    assert contents[2] == "bitcoin price\tneeds_internet\tstage2\t0.9100"


def test_write_tsv_drops_rows_with_embedded_tabs_or_newlines(
    extractor, tmp_path: Path
) -> None:
    """A query containing a tab or newline would corrupt the TSV; those
    rows are silently dropped rather than escaped — promotion is a
    human-review step anyway, and silent corruption is worse than a
    missing row."""
    Candidate = extractor.Candidate
    rows = [
        Candidate(query="clean query", label="x", stage="stage2", confidence=0.5),
        Candidate(query="dirty\tquery", label="x", stage="stage2", confidence=0.5),
        Candidate(query="another\nbroken", label="x", stage="stage2", confidence=0.5),
    ]
    output = tmp_path / "candidates.tsv"
    extractor.write_tsv(rows, output)
    contents = output.read_text(encoding="utf-8").splitlines()
    # Header + one clean row only
    assert len(contents) == 2
    assert "clean query" in contents[1]
