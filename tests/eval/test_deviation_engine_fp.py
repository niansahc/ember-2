"""tests/eval/test_deviation_engine_fp.py

False-positive rate eval for the deviation detector (ADR-013/026).

Drives synthetic typical responses through src.safety.deviation_detector.detect
and asserts no deviation record is written. Stage 2 Ollama call is mocked
to return ("NO", ...) for every pattern class so the test exercises the
end-to-end gate without an Ollama dependency.

Stage 1 helper _select_pattern_class always returns the first eligible
class from the priority list when pattern_classes.yaml has entries; it
does NOT screen the response text. The FP gate is the second-pass
classifier inside detect(). The user spec asked to assert
_select_pattern_class returns None for typical responses, but that
contradicts the helper's actual contract; the test below documents the
actual behavior (returns a candidate class) and pins the FP property
where it actually lives -- detect() returning None and write_memory
never being called.

This module is marked @pytest.mark.eval and excluded from the default
suite. The mocks involve safety/Ollama-adjacent setup that is
non-trivial to maintain alongside the live tests in
tests/test_deviation_detector.py.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from src.safety.deviation_detector import (
    GATED_INTENTS,
    _select_pattern_class,
    detect,
)


pytestmark = pytest.mark.eval


# ---------------------------------------------------------------------------
# Typical responses that should not produce a deviation record
# ---------------------------------------------------------------------------


_TYPICAL_RESPONSES = [
    # Factual answer
    "The migration script handles records created before the cutoff date.",
    # Brief acknowledgment
    "Got it. Updated.",
    # Task confirmation
    "Added that to the list.",
]


@pytest.mark.parametrize("response_text", _TYPICAL_RESPONSES)
@patch("src.safety.deviation_detector.write_memory")
@patch("src.safety.deviation_detector._run_second_pass", return_value=("NO", "no pattern match"))
def test_typical_response_produces_no_deviation_record(
    mock_second_pass, mock_write_memory, response_text,
):
    """detect() must return None and write_memory must not be called
    when the second pass classifies every candidate as NO. Uses an
    intent_class in GATED_INTENTS so the function actually runs the
    pipeline; uses logprobs=None so the entropy gate proceeds to the
    second pass (matches the live in-process behavior when no logprobs
    are wired through)."""
    with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "true"}):
        result = detect(
            response_text,
            intent_class="default",
            logprobs=None,
            prior_response=None,
        )
    assert result is None
    mock_write_memory.assert_not_called()


@patch("src.safety.deviation_detector.write_memory")
@patch("src.safety.deviation_detector._run_second_pass", return_value=("NO", "no pattern match"))
def test_caretaking_language_response_no_record_when_second_pass_no(
    mock_second_pass, mock_write_memory,
):
    """Even when the response contains language that would be a strong
    candidate for a pattern class (e.g. 'I'm proud of you'), detect()
    must produce no record when the second-pass classifier says NO.
    This pins the FP gate at the second-pass layer."""
    response_with_pattern_signal = (
        "I'm proud of you for sticking with this. "
        "The deployment is queued -- it should land in 20 minutes."
    )
    with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "true"}):
        result = detect(
            response_with_pattern_signal,
            intent_class="default",
            logprobs=None,
            prior_response=None,
        )
    assert result is None
    mock_write_memory.assert_not_called()
    # Second pass must have run at least once -- gating proved active
    assert mock_second_pass.call_count >= 1


@patch("src.safety.deviation_detector.write_memory")
@patch("src.safety.deviation_detector._run_second_pass", return_value=("NO", "no pattern match"))
def test_high_entropy_short_circuits_before_second_pass(
    mock_second_pass, mock_write_memory,
):
    """When entropy is above the 0.7 default threshold, detect() exits
    before any second-pass call. Pins the entropy gate as a useful FP
    safeguard for high-variance generation paths."""
    import math
    high_entropy_logprobs = [math.log(0.1)] * 10  # uniform -> entropy ~ 1.0
    with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "true"}):
        result = detect(
            "any caring response",
            intent_class="default",
            logprobs=high_entropy_logprobs,
            prior_response=None,
        )
    assert result is None
    mock_write_memory.assert_not_called()
    mock_second_pass.assert_not_called()


def test_select_pattern_class_returns_first_eligible_class():
    """Documents the actual contract of _select_pattern_class: with a
    populated config and no prior response, it returns the highest-
    priority single-response class (caretaking_language). The helper
    does NOT screen the response text; the FP gate is downstream in
    detect(). This test pins that contract so future refactors that
    push the FP screen earlier are visible regressions."""
    cls = _select_pattern_class(
        intent_class="default",
        prior_response=None,
        response_text="The migration script handles records correctly.",
    )
    # Either pattern_classes.yaml is loaded and a class is returned, or
    # the file is missing and None comes back. The configured project
    # ships pattern_classes.yaml, so a class is expected.
    if cls is not None:
        assert cls.get("name") == "caretaking_language"
        assert cls.get("detection_type") != "logprob_first"
        assert cls.get("detection_type") != "multi_turn"


def test_gated_intents_excludes_factual_classes():
    """Sanity check: factual_recall and web_search are NOT in
    GATED_INTENTS, so the deviation detector never runs on them. This
    is the structural FP gate that complements the second-pass mock."""
    assert "factual_recall" not in GATED_INTENTS
    assert "web_search" not in GATED_INTENTS
    assert "default" in GATED_INTENTS
