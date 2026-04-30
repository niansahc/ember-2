"""tests/test_eval_probe.py

Unit + integration tests for the answer-vs-packet fabrication detector.

Pure-function tests cover sentence segmentation, the second-person
anchor filter, cosine math, classify(), packet record extraction, and
log entry construction (privacy redaction).

Integration tests mock httpx (for /debug-context and /v1/chat/completions)
and embed_batch (for nomic-embed-text) so the pipeline can be exercised
end-to-end without a live API or Ollama dependency.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.eval_probe import (
    PROBE_QUESTIONS,
    PROBE_THRESHOLD,
    FlaggedSentence,
    PacketRecord,
    ProbeResult,
    build_log_entry,
    classify,
    cosine,
    cosine_max,
    extract_packet_records,
    filter_second_person_anchored,
    render_console_summary,
    run_probe_for_question,
    segment_sentences,
    write_probe_log,
)


# ---------------------------------------------------------------------------
# Sanity: probe question constants
# ---------------------------------------------------------------------------


def test_probe_questions_count_is_seven():
    assert len(PROBE_QUESTIONS) == 7


def test_probe_threshold_matches_spec():
    assert PROBE_THRESHOLD == 0.55


# ---------------------------------------------------------------------------
# segment_sentences
# ---------------------------------------------------------------------------


def test_segment_empty_string():
    assert segment_sentences("") == []
    assert segment_sentences("   ") == []


def test_segment_single_sentence():
    assert segment_sentences("This is one sentence.") == ["This is one sentence."]


def test_segment_three_sentences_with_terminators():
    text = "First sentence. Second sentence? Third sentence!"
    out = segment_sentences(text)
    assert out == ["First sentence.", "Second sentence?", "Third sentence!"]


def test_segment_does_not_split_on_lowercase_continuation():
    """Splitter requires capital letter after terminator -- 'i.e.' is
    safe."""
    text = "Use TLS, i.e. encryption. Then send."
    out = segment_sentences(text)
    # Should split before 'Then' but not at 'i.e.'
    assert out == ["Use TLS, i.e. encryption.", "Then send."]


# ---------------------------------------------------------------------------
# filter_second_person_anchored
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "You mentioned a project.",
        "Your project sounds important.",
        "Be kind to yourself.",
        "Yours is the third option.",
        "You're working hard.",
        "You've been busy.",
        "You'd benefit from rest.",
        "You'll figure it out.",
        "YOU mentioned this.",  # case insensitive
        "What about your day?",
    ],
)
def test_anchor_matches_second_person_forms(sentence):
    assert filter_second_person_anchored([sentence]) == [sentence]


@pytest.mark.parametrize(
    "sentence",
    [
        "Youth is wasted on the young.",
        "The yo-yo spun.",
        "I am working on a thing.",
        "We should ship it.",
        "They mentioned a project.",
    ],
)
def test_anchor_rejects_non_second_person(sentence):
    assert filter_second_person_anchored([sentence]) == []


def test_anchor_keeps_only_matching_subset():
    sentences = [
        "I think this is true.",
        "You mentioned a thing.",
        "Youth sports are great.",
        "Your work is good.",
    ]
    out = filter_second_person_anchored(sentences)
    assert out == ["You mentioned a thing.", "Your work is good."]


# ---------------------------------------------------------------------------
# cosine + cosine_max
# ---------------------------------------------------------------------------


def test_cosine_identical_vectors_is_one():
    v = [1.0, 0.0, 0.0]
    assert cosine(v, v) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine(a, b) == pytest.approx(0.0)


def test_cosine_antiparallel_is_minus_one():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert cosine(a, b) == pytest.approx(-1.0)


def test_cosine_zero_norm_returns_zero():
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine([1.0, 0.0], [0.0, 0.0]) == 0.0


def test_cosine_max_empty_records():
    score, idxs, scores = cosine_max([1.0, 0.0], [])
    assert score == 0.0
    assert idxs == []
    assert scores == []


def test_cosine_max_returns_top_3_descending():
    claim = [1.0, 0.0, 0.0]
    records = [
        [0.0, 1.0, 0.0],   # cos=0
        [1.0, 0.0, 0.0],   # cos=1
        [0.5, 0.5, 0.0],   # cos~0.707
        [0.9, 0.1, 0.0],   # cos~0.994
    ]
    score, idxs, scores = cosine_max(claim, records)
    # Top score is index 1 (cos=1)
    assert score == pytest.approx(1.0)
    # Top-3 indices should be [1, 3, 2] in that order
    assert idxs == [1, 3, 2]
    # Scores should be descending
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


def test_classify_at_threshold_is_grounded():
    assert classify(0.55) == "GROUNDED"


def test_classify_just_below_threshold_is_fabricated():
    assert classify(0.5499) == "FABRICATED"


def test_classify_high_confidence_grounded():
    assert classify(0.95) == "GROUNDED"


def test_classify_zero_is_fabricated():
    assert classify(0.0) == "FABRICATED"


def test_classify_negative_is_fabricated():
    assert classify(-0.3) == "FABRICATED"


# ---------------------------------------------------------------------------
# extract_packet_records
# ---------------------------------------------------------------------------


def test_extract_records_from_full_packet():
    packet = {
        "memory_items": [
            {
                "id": "m1",
                "content": "User mentioned a Python migration.",
                "memory_type": "conversation",
                "timestamp": "2026-04-01T10-00-00",
            }
        ],
        "reflection_items": [
            {
                "id": "r1",
                "content": "User has been focused on shipping.",
                "timestamp": "2026-04-15T18-00-00",
            }
        ],
        "state_items": [
            {
                "category": "current_focus",
                "text": "shipping v0.18.0",
                "timestamp": "2026-04-30T12-00-00",
            }
        ],
    }
    records = extract_packet_records(packet)
    assert len(records) == 3
    assert records[0].text == "User mentioned a Python migration."
    assert records[0].memory_type == "conversation"
    assert records[1].text == "User has been focused on shipping."
    assert records[1].memory_type == "reflection"
    assert records[2].text == "shipping v0.18.0"
    assert records[2].memory_type == "state"
    assert records[2].record_id == "state_current_focus"


def test_extract_skips_empty_text():
    packet = {
        "memory_items": [
            {"id": "m1", "content": "", "memory_type": "conversation", "timestamp": ""},
            {"id": "m2", "content": "   ", "memory_type": "conversation", "timestamp": ""},
            {"id": "m3", "content": "real text", "memory_type": "conversation", "timestamp": ""},
        ],
        "reflection_items": [],
        "state_items": [],
    }
    records = extract_packet_records(packet)
    assert len(records) == 1
    assert records[0].text == "real text"


def test_extract_handles_missing_keys():
    """Empty packet should produce empty records list, not raise."""
    assert extract_packet_records({}) == []


# ---------------------------------------------------------------------------
# build_log_entry
# ---------------------------------------------------------------------------


def _make_result(verdict="GROUNDED", flagged=None, records=None):
    """Helper for log entry tests."""
    flagged = flagged or []
    records = records or []
    return ProbeResult(
        question="What do you know about me?",
        verdict=verdict,
        anchored_sentence_count=2,
        fabricated_sentence_count=len(flagged),
        grounded_sentence_count=2 - len(flagged),
        packet_record_count=len(records),
        flagged_sentences=flagged,
        records=records,
    )


def test_log_entry_no_flags_grounded():
    entry = build_log_entry(_make_result("GROUNDED"))
    assert entry["verdict"] == "GROUNDED"
    assert entry["flagged_sentences"] == []
    assert entry["question"] == "What do you know about me?"


def test_log_entry_flagged_with_sentence_text_default():
    records = [
        PacketRecord(text="something", record_id="m1", memory_type="conversation", timestamp="t1"),
        PacketRecord(text="other", record_id="m2", memory_type="state", timestamp="t2"),
    ]
    flagged = [
        FlaggedSentence(
            sentence="You're working on a Ruby project.",
            max_cosine=0.42,
            top_3_record_indices=[0, 1],
            top_3_cosines=[0.42, 0.30],
        ),
    ]
    entry = build_log_entry(_make_result("FABRICATED", flagged, records), log_sentences=True)
    assert entry["verdict"] == "FABRICATED"
    fs_entry = entry["flagged_sentences"][0]
    assert fs_entry["sentence"] == "You're working on a Ruby project."
    assert fs_entry["max_cosine"] == 0.42
    assert fs_entry["threshold"] == PROBE_THRESHOLD
    assert len(fs_entry["top_3_records"]) == 2
    # No record content -- only metadata
    assert "text" not in fs_entry["top_3_records"][0]
    assert "content" not in fs_entry["top_3_records"][0]
    assert fs_entry["top_3_records"][0]["memory_type"] == "conversation"
    assert fs_entry["top_3_records"][0]["id"] == "m1"


def test_log_entry_flagged_with_sentence_redacted():
    records = [PacketRecord(text="x", record_id="m1", memory_type="memory", timestamp="t1")]
    flagged = [
        FlaggedSentence(
            sentence="You're working on a Ruby project.",
            max_cosine=0.42,
            top_3_record_indices=[0],
            top_3_cosines=[0.42],
        ),
    ]
    entry = build_log_entry(
        _make_result("FABRICATED", flagged, records),
        log_sentences=False,
    )
    fs_entry = entry["flagged_sentences"][0]
    # Sentence text is NOT in the redacted form
    assert "sentence" not in fs_entry
    assert fs_entry["sentence_redacted"] is True
    assert fs_entry["sentence_length"] == len("You're working on a Ruby project.")


def test_log_entry_strips_non_ascii_in_sentence():
    """ASCII coercion: em dashes and curly quotes get dropped."""
    records = [PacketRecord(text="x", record_id="m1", memory_type="memory", timestamp="t1")]
    # Construct the test sentence using chr() at runtime so the source
    # file stays pure ASCII. U+2019 = right single quote (curly
    # apostrophe). U+2014 = em dash. The runtime string contains the
    # non-ASCII chars; ASCII coercion in build_log_entry must strip
    # them.
    curly_apos = chr(0x2019)
    em_dash = chr(0x2014)
    test_sentence = f"You{curly_apos}re working on a thing{em_dash}a project."
    flagged = [
        FlaggedSentence(
            sentence=test_sentence,
            max_cosine=0.4,
            top_3_record_indices=[0],
            top_3_cosines=[0.4],
        ),
    ]
    entry = build_log_entry(_make_result("FABRICATED", flagged, records))
    sent = entry["flagged_sentences"][0]["sentence"]
    # No non-ASCII characters survive
    assert all(ord(c) < 128 for c in sent)
    # The straight quote substitution is fine, em dash is stripped
    assert "Youre working on" in sent or "You're working on" in sent
    # Content survives in some form
    assert "a project" in sent


# ---------------------------------------------------------------------------
# Integration: run_probe_for_question with mocks
# ---------------------------------------------------------------------------


def _mock_packet_response(records: list[dict]) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {
        "memory_items": records,
        "reflection_items": [],
        "state_items": [],
    }
    response.raise_for_status.return_value = None
    return response


def _mock_chat_response(content: str) -> MagicMock:
    response = MagicMock()
    response.json.return_value = {
        "choices": [{"message": {"content": content}}],
    }
    response.raise_for_status.return_value = None
    return response


def test_pipeline_grounded_path():
    """Anchored sentence with high cosine to a packet record -> GROUNDED."""
    packet_records = [
        {"id": "m1", "content": "User has a Python migration.", "memory_type": "conversation", "timestamp": "t1"},
    ]
    answer = "You're working on a Python migration."

    sentence_emb = [1.0, 0.0, 0.0]
    record_emb = [0.95, 0.05, 0.0]   # cos ~ 0.998

    with patch("tools.eval_probe.httpx.get", return_value=_mock_packet_response(packet_records)), \
         patch("tools.eval_probe.httpx.post", return_value=_mock_chat_response(answer)), \
         patch("tools.eval_probe.embed_batch", side_effect=[[sentence_emb], [record_emb]]):
        result = run_probe_for_question("What am I working on?", "http://localhost:8000", "test-key")

    assert result.verdict == "GROUNDED"
    assert result.anchored_sentence_count == 1
    assert result.fabricated_sentence_count == 0
    assert result.grounded_sentence_count == 1


def test_pipeline_fabricated_path():
    """Anchored sentence with low cosine to records -> FABRICATED."""
    packet_records = [
        {"id": "m1", "content": "Unrelated content.", "memory_type": "conversation", "timestamp": "t1"},
    ]
    answer = "You're working on a Ruby migration."

    sentence_emb = [1.0, 0.0, 0.0]
    record_emb = [0.0, 1.0, 0.0]   # cos = 0

    with patch("tools.eval_probe.httpx.get", return_value=_mock_packet_response(packet_records)), \
         patch("tools.eval_probe.httpx.post", return_value=_mock_chat_response(answer)), \
         patch("tools.eval_probe.embed_batch", side_effect=[[sentence_emb], [record_emb]]):
        result = run_probe_for_question("What am I working on?", "http://localhost:8000", "test-key")

    assert result.verdict == "FABRICATED"
    assert result.fabricated_sentence_count == 1
    assert len(result.flagged_sentences) == 1
    assert result.flagged_sentences[0].max_cosine == pytest.approx(0.0)


def test_pipeline_empty_packet_flags_all_anchored():
    """Empty packet means every anchored sentence is by definition
    ungrounded (max_cosine=0.0 against zero records)."""
    answer = "You're working on a project. Your day is full."

    with patch("tools.eval_probe.httpx.get", return_value=_mock_packet_response([])), \
         patch("tools.eval_probe.httpx.post", return_value=_mock_chat_response(answer)), \
         patch("tools.eval_probe.embed_batch", side_effect=[[[1.0], [0.5]], []]):
        result = run_probe_for_question("Q", "http://localhost:8000", "test-key")

    assert result.verdict == "FABRICATED"
    assert result.anchored_sentence_count == 2
    assert result.fabricated_sentence_count == 2
    assert result.packet_record_count == 0


def test_pipeline_no_anchored_sentences_grounded():
    """Answer with no second-person anchors short-circuits to GROUNDED
    with anchored_sentence_count=0 (model made no personal claims)."""
    packet_records = [
        {"id": "m1", "content": "x", "memory_type": "conversation", "timestamp": "t"},
    ]
    answer = "The migration is going well. It should ship soon."

    with patch("tools.eval_probe.httpx.get", return_value=_mock_packet_response(packet_records)), \
         patch("tools.eval_probe.httpx.post", return_value=_mock_chat_response(answer)), \
         patch("tools.eval_probe.embed_batch") as mock_embed:
        result = run_probe_for_question("Q", "http://localhost:8000", "test-key")

    assert result.verdict == "GROUNDED"
    assert result.anchored_sentence_count == 0
    assert result.fabricated_sentence_count == 0
    # embed_batch should not have been called -- no anchored sentences
    mock_embed.assert_not_called()


def test_pipeline_packet_fetch_error_returns_error_verdict():
    """If /debug-context fails, verdict=ERROR with stage=fetch_packet
    and the battery (caller) can continue."""
    with patch("tools.eval_probe.httpx.get", side_effect=Exception("connection refused")):
        result = run_probe_for_question("Q", "http://localhost:8000", "test-key")

    assert result.verdict == "ERROR"
    assert result.error_stage == "fetch_packet"
    assert "connection refused" in (result.error_message or "")


def test_pipeline_answer_fetch_error_returns_error_verdict():
    """If chat completions fails, verdict=ERROR with stage=fetch_answer."""
    packet_records = [
        {"id": "m1", "content": "x", "memory_type": "conversation", "timestamp": "t"},
    ]
    with patch("tools.eval_probe.httpx.get", return_value=_mock_packet_response(packet_records)), \
         patch("tools.eval_probe.httpx.post", side_effect=Exception("API timeout")):
        result = run_probe_for_question("Q", "http://localhost:8000", "test-key")

    assert result.verdict == "ERROR"
    assert result.error_stage == "fetch_answer"
    # Records were captured before the answer-fetch failure
    assert result.packet_record_count == 1


def test_pipeline_embed_error_returns_error_verdict():
    packet_records = [
        {"id": "m1", "content": "x", "memory_type": "conversation", "timestamp": "t"},
    ]
    answer = "You mentioned a thing."
    with patch("tools.eval_probe.httpx.get", return_value=_mock_packet_response(packet_records)), \
         patch("tools.eval_probe.httpx.post", return_value=_mock_chat_response(answer)), \
         patch("tools.eval_probe.embed_batch", side_effect=Exception("Ollama down")):
        result = run_probe_for_question("Q", "http://localhost:8000", "test-key")

    assert result.verdict == "ERROR"
    assert result.error_stage == "embed"


# ---------------------------------------------------------------------------
# write_probe_log
# ---------------------------------------------------------------------------


def test_write_probe_log_creates_summary_and_per_question_files(tmp_path):
    results = [
        _make_result("GROUNDED"),
        _make_result(
            "FABRICATED",
            flagged=[
                FlaggedSentence(
                    sentence="You're inventing a thing.",
                    max_cosine=0.3,
                    top_3_record_indices=[0],
                    top_3_cosines=[0.3],
                ),
            ],
            records=[
                PacketRecord(text="x", record_id="m1", memory_type="conversation", timestamp="t"),
            ],
        ),
    ]
    run_dir = write_probe_log(results, log_sentences=True, log_root=tmp_path)
    assert run_dir.exists()
    summary_path = run_dir / "summary.json"
    assert summary_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["total_questions"] == 2
    assert summary["questions_with_flags"] == 1
    # Per-question files
    files = sorted(run_dir.glob("q*.json"))
    assert len(files) == 2


def test_write_probe_log_with_redacted_sentences(tmp_path):
    results = [
        _make_result(
            "FABRICATED",
            flagged=[
                FlaggedSentence(
                    sentence="You're inventing a thing.",
                    max_cosine=0.3,
                    top_3_record_indices=[0],
                    top_3_cosines=[0.3],
                ),
            ],
            records=[
                PacketRecord(text="x", record_id="m1", memory_type="conversation", timestamp="t"),
            ],
        ),
    ]
    run_dir = write_probe_log(results, log_sentences=False, log_root=tmp_path)
    files = sorted(run_dir.glob("q*.json"))
    entry = json.loads(files[0].read_text(encoding="utf-8"))
    fs = entry["flagged_sentences"][0]
    assert "sentence" not in fs
    assert fs["sentence_redacted"] is True
    assert fs["sentence_length"] > 0


# ---------------------------------------------------------------------------
# render_console_summary
# ---------------------------------------------------------------------------


def test_console_summary_all_grounded():
    results = [_make_result("GROUNDED")]
    out = render_console_summary(results)
    assert "GROUNDED" in out
    assert "All probe questions GROUNDED" in out


def test_console_summary_with_flags():
    results = [
        _make_result("GROUNDED"),
        _make_result("FABRICATED", flagged=[
            FlaggedSentence("x", 0.0, [], []),
        ]),
    ]
    out = render_console_summary(results)
    assert "MANUAL REVIEW REQUIRED" in out
    assert "1 question" in out


def test_console_summary_is_ascii_only():
    """No em dashes, smart quotes, or arrows in the summary block."""
    results = [_make_result("GROUNDED")]
    out = render_console_summary(results)
    assert all(ord(c) < 128 for c in out)
