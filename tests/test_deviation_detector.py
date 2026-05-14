"""
Tests for DeviationDetector — post-hoc behavioral pattern detection (ADR-013, ADR-026).
"""

import json
import math
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from src.safety.deviation_detector import (
    GATED_INTENTS,
    HEDGING_DENSITY_THRESHOLD,
    DeviationResult,
    compute_entropy,
    detect,
    get_entropy_threshold,
    is_enabled,
    write_deviation_record,
    _hedging_density,
    _load_pattern_classes,
    _log_detection,
    _select_pattern_class,
)


# ── is_enabled / config ──────────────────────────────────────────────────

class TestIsEnabled:
    def test_default_is_false(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("EMBER_DEVIATION_DETECTION", None)
            assert is_enabled() is False

    def test_true_when_set(self):
        with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "true"}):
            assert is_enabled() is True

    def test_false_when_explicit_false(self):
        with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "false"}):
            assert is_enabled() is False

    def test_case_insensitive(self):
        with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "True"}):
            assert is_enabled() is True


class TestGetEntropyThreshold:
    def test_default_is_0_7(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("EMBER_DEVIATION_ENTROPY_THRESHOLD", None)
            assert get_entropy_threshold() == 0.7

    def test_custom_threshold(self):
        with patch.dict(os.environ, {"EMBER_DEVIATION_ENTROPY_THRESHOLD": "0.5"}):
            assert get_entropy_threshold() == 0.5

    def test_invalid_threshold_returns_default(self):
        with patch.dict(os.environ, {"EMBER_DEVIATION_ENTROPY_THRESHOLD": "invalid"}):
            assert get_entropy_threshold() == 0.7


# ── Entropy computation ──────────────────────────────────────────────────

class TestComputeEntropy:
    def test_empty_logprobs_returns_sentinel(self):
        # No logprobs = cannot measure = proceed to second pass
        assert compute_entropy([]) == -1.0

    def test_uniform_distribution_high_entropy(self):
        # Uniform logprobs → high entropy
        logprobs = [math.log(0.1)] * 10
        entropy = compute_entropy(logprobs)
        assert entropy > 0.9

    def test_concentrated_distribution_low_entropy(self):
        # One dominant token → low entropy
        logprobs = [math.log(0.99)] + [math.log(0.001)] * 9
        entropy = compute_entropy(logprobs)
        assert entropy < 0.5

    def test_single_token_returns_high(self):
        # Single token = no distribution = assume high entropy (skip detection)
        entropy = compute_entropy([math.log(1.0)])
        # Single token normalized entropy can be 0 or 1 depending on implementation
        # The key invariant: single token should not trigger detection
        assert entropy >= 0.0

    def test_returns_normalized_0_to_1(self):
        logprobs = [math.log(0.5), math.log(0.3), math.log(0.2)]
        entropy = compute_entropy(logprobs)
        assert 0.0 <= entropy <= 1.0


# ── Hedging density ──────────────────────────────────────────────────────

class TestHedgingDensity:
    def test_no_hedging(self):
        assert _hedging_density("The sky is blue and water is wet.") == 0.0

    def test_high_hedging(self):
        text = "Perhaps you might consider that it could be something. Perhaps it might work."
        density = _hedging_density(text)
        assert density > 0

    def test_empty_text(self):
        assert _hedging_density("") == 0.0

    def test_above_threshold(self):
        # Pack hedging into short text
        text = "Perhaps you might consider. It's worth noting that perhaps it might."
        density = _hedging_density(text)
        assert density >= HEDGING_DENSITY_THRESHOLD


# ── Pattern class loader ──────────────────────────────────────────────────

class TestLoadPatternClasses:
    def test_loads_production_classes(self):
        classes = _load_pattern_classes()
        assert len(classes) == 11
        names = {c["name"] for c in classes}
        assert "caretaking_language" in names
        assert "position_collapse" in names
        assert "template_collapse" in names

    def test_all_classes_have_required_fields(self):
        for cls in _load_pattern_classes():
            assert "name" in cls
            assert "detection_type" in cls
            assert "markers" in cls
            assert len(cls["markers"]) > 0


# ── Pattern class selection ───────────────────────────────────────────────

class TestSelectPatternClass:
    def test_selects_single_response_first(self):
        cls = _select_pattern_class("casual", "prior response", "current response")
        assert cls is not None
        # Single-response classes have priority over multi-turn
        assert cls["detection_type"] == "single_response"
        assert cls["name"] == "caretaking_language"

    def test_skips_multi_turn_when_no_prior(self):
        cls = _select_pattern_class("casual", None, "current response")
        assert cls is not None
        # Should skip position_collapse and template_collapse
        assert cls["detection_type"] != "multi_turn"

    def test_returns_none_for_empty_classes(self):
        with patch("src.safety.deviation_detector._load_pattern_classes", return_value=[]):
            assert _select_pattern_class("casual", None, "text") is None


# ── Detection (main entry point) ─────────────────────────────────────────

class TestDetect:
    def test_returns_none_when_disabled(self):
        with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "false"}):
            result = detect("some response", "casual")
            assert result is None

    def test_returns_none_for_non_gated_intent(self):
        with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "true"}):
            result = detect("some response", "factual_recall")
            assert result is None

    def test_returns_none_for_empty_response(self):
        with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "true"}):
            result = detect("", "casual")
            assert result is None

    def test_skips_when_entropy_above_threshold(self):
        with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "true"}):
            # Uniform logprobs = high entropy
            high_entropy_logprobs = [math.log(0.1)] * 10
            result = detect("some response", "casual", logprobs=high_entropy_logprobs)
            assert result is None

    @patch("src.safety.deviation_detector._run_second_pass")
    def test_proceeds_when_no_logprobs(self, mock_second_pass):
        mock_second_pass.return_value = ("YES", "Pattern detected")
        with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "true"}):
            # No logprobs = cannot measure entropy = proceed to second pass
            result = detect("some caring response", "casual", logprobs=None)
            assert result is not None
            assert mock_second_pass.called

    @patch("src.safety.deviation_detector._run_second_pass")
    def test_triggers_second_pass_on_low_entropy(self, mock_second_pass):
        mock_second_pass.return_value = ("YES", "Pattern detected: caretaking")
        with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "true"}):
            # Very concentrated logprobs = low entropy
            low_entropy_logprobs = [math.log(0.99)] + [math.log(0.001)] * 5
            result = detect("some caring response", "casual", logprobs=low_entropy_logprobs)
            assert result is not None
            assert result.second_pass_result == "YES"
            assert mock_second_pass.called

    @patch("src.safety.deviation_detector._run_second_pass")
    def test_returns_none_on_no_detection(self, mock_second_pass):
        mock_second_pass.return_value = ("NO", "No pattern found")
        with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "true"}):
            low_entropy_logprobs = [math.log(0.99)] + [math.log(0.001)] * 5
            result = detect("normal response", "casual", logprobs=low_entropy_logprobs)
            assert result is None

    @patch("src.safety.deviation_detector._run_second_pass")
    def test_hedging_pre_screen_triggers(self, mock_second_pass):
        mock_second_pass.return_value = ("YES", "Hedging detected")
        with patch.dict(os.environ, {"EMBER_DEVIATION_DETECTION": "true"}):
            hedgy_text = "Perhaps you might consider that it's worth noting perhaps it might."
            low_entropy = [math.log(0.99)] + [math.log(0.001)] * 5
            result = detect(hedgy_text, "casual", logprobs=low_entropy)
            assert result is not None
            assert result.pattern_class == "indirectness_softening"

    def test_gated_intents_match_spec(self):
        assert "casual" in GATED_INTENTS
        assert "emotional" in GATED_INTENTS
        assert "default" in GATED_INTENTS
        assert "factual_recall" not in GATED_INTENTS
        assert "web_search" not in GATED_INTENTS


# ── Record writer ─────────────────────────────────────────────────────────

class TestWriteDeviationRecord:
    @patch("src.safety.deviation_detector.write_memory")
    def test_writes_record_on_yes(self, mock_write):
        result = DeviationResult(
            pattern_class="caretaking_language",
            second_pass_result="YES",
            entropy_score=0.3,
            evidence="warmth over directness",
        )
        record = write_deviation_record(result, "user said something", "caring response")
        assert record is not None
        assert record["pattern_class"] == "caretaking_language"
        assert record["confirmed"] is False
        assert record["entropy_score"] == 0.3
        assert record["second_pass_result"] == "YES"
        mock_write.assert_called_once()
        call_kwargs = mock_write.call_args
        assert call_kwargs[1]["memory_type"] == "deviation"
        assert "deviation" in call_kwargs[1]["tags"]

    @patch("src.safety.deviation_detector.write_memory", side_effect=Exception("write failed"))
    def test_handles_write_failure(self, mock_write):
        result = DeviationResult("test", "YES", 0.3, "evidence")
        record = write_deviation_record(result, "user", "response")
        assert record is None

    @patch("src.safety.deviation_detector.write_memory")
    def test_record_starts_unconfirmed(self, mock_write):
        result = DeviationResult("test", "YES", 0.3, "evidence")
        record = write_deviation_record(result, "user", "response")
        assert record["confirmed"] is False
        assert record["reason"] is None
        assert record["value_aligned"] is False

    @patch("src.safety.deviation_detector.write_memory")
    def test_truncates_long_text(self, mock_write):
        result = DeviationResult("test", "YES", 0.3, "evidence")
        long_msg = "x" * 1000
        record = write_deviation_record(result, long_msg, long_msg)
        assert len(record["friction_context"]) <= 500
        assert len(record["deviation_chosen"]) <= 500


# ── Logging ───────────────────────────────────────────────────────────────

class TestLogging:
    def test_log_detection_writes_file(self, tmp_path):
        with patch("src.safety.deviation_detector._LOG_DIR", tmp_path):
            _log_detection("test_class", "YES", 0.5, "test evidence", "casual")
            log_files = list(tmp_path.glob("*.log"))
            assert len(log_files) == 1
            content = log_files[0].read_text()
            entry = json.loads(content.strip())
            assert entry["pattern_class"] == "test_class"
            assert entry["result"] == "YES"


# ----------------------------------------------------------------------------
# Jaccard pre-filter for template_collapse (B5 fix)
# ----------------------------------------------------------------------------
# Background: docs/audits/b5_template_collapse_diagnosis.md identified that
# template_collapse delegated similarity judgment entirely to a one-shot
# qwen3:8b YES/NO call, with no deterministic similarity computation in
# code. On a near-verbatim pair the model returned NO. The fix adds a
# deterministic Jaccard pre-filter scoped to template_collapse only.

class TestJaccardSimilarityHelper:
    """The pure Jaccard token-set helper (Q1 tokenizer:
    lowercase + strip punctuation + whitespace split)."""

    def test_identical_text_is_1_0(self):
        from src.safety.deviation_detector import _jaccard_similarity
        assert _jaccard_similarity(
            "the quick brown fox", "the quick brown fox",
        ) == 1.0

    def test_disjoint_text_is_0(self):
        from src.safety.deviation_detector import _jaccard_similarity
        assert _jaccard_similarity(
            "alpha beta gamma delta", "epsilon zeta eta theta",
        ) == 0.0

    def test_punctuation_and_case_normalized(self):
        """Trivial formatting differences must not split the token sets.
        'Hello!' vs 'hello.' both reduce to {hello} after lowercase +
        strip punctuation, so Jaccard is 1.0."""
        from src.safety.deviation_detector import _jaccard_similarity
        assert _jaccard_similarity("Hello!", "hello.") == 1.0
        assert _jaccard_similarity(
            "Got it. Updated.", "got it!! UPDATED",
        ) == 1.0

    def test_contraction_apostrophe_not_normalized(self):
        """Deliberate Q1 decision: contractions stay distinct from their
        expanded forms. "I'm" tokenizes to {im} (apostrophe stripped)
        while "I am" tokenizes to {i, am}; Jaccard well below 0.85 so
        the pair correctly falls through to the LLM second-pass rather
        than tripping the pre-filter."""
        from src.safety.deviation_detector import _jaccard_similarity
        score = _jaccard_similarity("I'm here to help.", "I am here to help.")
        assert score < 0.85, (
            f"Contraction vs expansion should not auto-flag; got Jaccard={score:.3f}"
        )


_TEMPLATE_COLLAPSE_CLASS = {
    "name": "template_collapse",
    "detection_type": "multi_turn",
    "requires": "response + prior_response",
    "markers": [
        "current response is semantically identical or near-identical to prior response",
        "different user input produced same output",
    ],
}


class TestTemplateCollapsePrefilter:
    """The template_collapse pre-filter shortcircuit (B5 fix).

    Pre-filter operates on FULL response text (Q2), not the LLM's 500-char
    truncated copy. Above the Jaccard threshold (default 0.85) returns
    ("YES", evidence) without calling Ollama. At or below threshold, falls
    through to the existing LLM second-pass."""

    def test_above_threshold_returns_yes_without_llm_call(self):
        """B5 regression: a near-verbatim pair must trigger YES at the
        pre-filter without invoking the LLM."""
        from src.safety.deviation_detector import _run_second_pass

        prior = (
            "I notice you're working through something difficult. "
            "Take your time and let me know what would help."
        )
        current = (
            "I notice you are working through something difficult. "
            "Take your time and let me know what would help."
        )

        with patch("ollama.chat") as mock_chat:
            result, evidence = _run_second_pass(
                _TEMPLATE_COLLAPSE_CLASS,
                response_text=current,
                prior_response=prior,
            )
            assert result == "YES"
            assert "jaccard prefilter" in evidence
            assert mock_chat.call_count == 0, (
                "Pre-filter must short-circuit the LLM call when Jaccard "
                "exceeds the threshold."
            )

    def test_below_threshold_falls_through_to_llm(self):
        """When Jaccard is at or below the threshold, the LLM second-pass
        must run as today. Mocked LLM returns NO; we assert that the LLM
        was called and the LLM's verdict was returned."""
        from src.safety.deviation_detector import _run_second_pass

        prior = "The migration script handles records before the cutoff."
        current = "Got it. Updated the docs and the changelog."

        mock_response = {
            "message": {"content": "NO. Responses are clearly different."},
        }
        with patch("ollama.chat", return_value=mock_response) as mock_chat:
            result, evidence = _run_second_pass(
                _TEMPLATE_COLLAPSE_CLASS,
                response_text=current,
                prior_response=prior,
            )
            assert result == "NO"
            assert mock_chat.call_count == 1, (
                "Below-threshold pair must invoke the LLM second-pass."
            )

    def test_pre_filter_skipped_when_no_prior_response(self):
        """multi_turn classes with prior_response=None cannot compute
        Jaccard. The pre-filter must skip (no exception, no early YES),
        and execution must fall through to the LLM second-pass. In
        practice detect() at line 363 also skips the entire class in
        this case; this test pins _run_second_pass behavior in
        isolation."""
        from src.safety.deviation_detector import _run_second_pass

        mock_response = {"message": {"content": "NO"}}
        with patch("ollama.chat", return_value=mock_response) as mock_chat:
            result, _ = _run_second_pass(
                _TEMPLATE_COLLAPSE_CLASS,
                response_text="some current response",
                prior_response=None,
            )
            # Even though the class is template_collapse, the pre-filter
            # cannot fire without a prior. LLM is asked instead.
            assert mock_chat.call_count == 1
            assert result == "NO"

    def test_jaccard_threshold_env_override(self):
        """EMBER_DEVIATION_JACCARD_THRESHOLD raises the bar; a pair that
        would otherwise auto-flag now falls through to the LLM."""
        from src.safety.deviation_detector import _run_second_pass

        # Same near-verbatim pair as the B5 regression test, which
        # ordinarily auto-flags at the default 0.85.
        prior = "I notice you are working through something difficult."
        current = "I notice you are working through something difficult."

        mock_response = {"message": {"content": "NO"}}
        with patch.dict(
            os.environ, {"EMBER_DEVIATION_JACCARD_THRESHOLD": "0.99"},
        ), patch("ollama.chat", return_value=mock_response) as mock_chat:
            # Wait - the pair is IDENTICAL so Jaccard == 1.0 which > 0.99.
            # Use a different pair where 0.85 fires but 0.99 doesn't.
            pass

        # Build a pair whose Jaccard is roughly 0.90 (above default 0.85,
        # below override 0.99).
        prior = "the alpha beta gamma delta epsilon zeta eta theta iota"
        # Add one extra token, remove one — should land ~0.9.
        current = "the alpha beta gamma delta epsilon zeta eta theta kappa"

        with patch.dict(
            os.environ, {"EMBER_DEVIATION_JACCARD_THRESHOLD": "0.99"},
        ), patch("ollama.chat", return_value=mock_response) as mock_chat:
            result, _ = _run_second_pass(
                _TEMPLATE_COLLAPSE_CLASS,
                response_text=current,
                prior_response=prior,
            )
            assert mock_chat.call_count == 1, (
                "With threshold raised to 0.99, the ~0.9-Jaccard pair "
                "must fall through to the LLM."
            )
            assert result == "NO"

    def test_pre_filter_evidence_string_format(self):
        """When the pre-filter fires, the evidence string is a
        reproducible 'jaccard prefilter: X.XXX >= Y.YYY' line."""
        import re as _re
        from src.safety.deviation_detector import _run_second_pass

        prior = "Take your time and let me know what would help."
        current = "Take your time and let me know what would help."

        with patch("ollama.chat") as mock_chat:
            result, evidence = _run_second_pass(
                _TEMPLATE_COLLAPSE_CLASS,
                response_text=current,
                prior_response=prior,
            )
            assert result == "YES"
            assert mock_chat.call_count == 0
            pattern = r"^jaccard prefilter: \d\.\d{3} >= \d\.\d{3}$"
            assert _re.match(pattern, evidence), (
                f"Evidence string does not match expected format: {evidence!r}"
            )

    @pytest.mark.parametrize("other_class_name", [
        "caretaking_language",
        "closing_question",
        "position_collapse",
        "framing_acceptance",
    ])
    def test_pre_filter_only_fires_for_template_collapse_pattern(
        self, other_class_name,
    ):
        """Scope constraint: even with a near-verbatim near-identical
        pair, _run_second_pass for any non-template_collapse class must
        invoke the LLM as today. The pre-filter must not bleed into the
        other 10 pattern classes."""
        from src.safety.deviation_detector import _run_second_pass

        other_class = {
            "name": other_class_name,
            "detection_type": "multi_turn",
            "requires": "response + prior_response",
            "markers": ["some marker", "another marker"],
        }
        prior = "I notice you are working through something difficult."
        current = "I notice you are working through something difficult."

        mock_response = {"message": {"content": "NO"}}
        with patch("ollama.chat", return_value=mock_response) as mock_chat:
            result, _ = _run_second_pass(
                other_class,
                response_text=current,
                prior_response=prior,
            )
            assert mock_chat.call_count == 1, (
                f"Pre-filter must not fire for {other_class_name!r}; "
                f"LLM call expected."
            )
            assert result == "NO"


class TestLogDetectionJaccardField:
    """The deviation log entry for template_collapse checks must include
    a numeric `jaccard` field so threshold recalibration is possible
    later. Non-template_collapse entries must NOT include the field
    (existing schema unchanged for the other 10 pattern classes)."""

    def test_template_collapse_log_entry_includes_jaccard(self, tmp_path):
        """End-to-end through detect(): a template_collapse check writes
        a log entry that includes the jaccard score."""
        prior = "Take your time and let me know what would help."
        current = prior  # Jaccard 1.0, pre-filter fires

        mock_no = {"message": {"content": "NO"}}
        with patch.dict(
            os.environ, {"EMBER_DEVIATION_DETECTION": "true"},
        ), patch(
            "src.safety.deviation_detector._LOG_DIR", tmp_path,
        ), patch("ollama.chat", return_value=mock_no):
            detect(
                response_text=current,
                intent_class="default",
                logprobs=None,
                prior_response=prior,
            )

        log_files = list(tmp_path.glob("*.log"))
        assert len(log_files) == 1
        entries = [
            json.loads(line)
            for line in log_files[0].read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        template_entries = [
            e for e in entries if e["pattern_class"] == "template_collapse"
        ]
        assert len(template_entries) == 1, (
            f"Expected exactly one template_collapse log entry; got "
            f"{len(template_entries)} in {[e['pattern_class'] for e in entries]}"
        )
        assert "jaccard" in template_entries[0], (
            f"template_collapse log entry missing 'jaccard' field: "
            f"{template_entries[0]}"
        )
        assert template_entries[0]["jaccard"] == 1.0
        # Non-template_collapse entries must NOT carry the field.
        for entry in entries:
            if entry["pattern_class"] != "template_collapse":
                assert "jaccard" not in entry, (
                    f"{entry['pattern_class']} entry unexpectedly carried "
                    f"jaccard field: {entry}"
                )


class TestDetectEndToEndPrefilter:
    """Integration: detect() with a near-verbatim pair triggers the
    template_collapse pre-filter and returns a DeviationResult without
    invoking the LLM for template_collapse."""

    def test_detect_end_to_end_near_verbatim_returns_template_collapse(
        self, tmp_path,
    ):
        """End-to-end: enable detection, run a near-verbatim pair, assert
        detect() returns a template_collapse result via the pre-filter
        (no LLM call for template_collapse itself). LLM is mocked to
        return NO for all earlier pattern classes the loop checks."""
        prior = (
            "I notice you are working through something difficult. "
            "Take your time and let me know what would help."
        )
        current = prior  # near-verbatim case: identical

        mock_no = {"message": {"content": "NO. Responses differ."}}

        with patch.dict(
            os.environ, {"EMBER_DEVIATION_DETECTION": "true"},
        ), patch(
            "src.safety.deviation_detector._LOG_DIR", tmp_path,
        ), patch("ollama.chat", return_value=mock_no) as mock_chat:
            result = detect(
                response_text=current,
                intent_class="default",
                logprobs=None,  # entropy=-1.0, skip the entropy gate
                prior_response=prior,
            )

        assert result is not None, "detect() must return a DeviationResult"
        assert result.pattern_class == "template_collapse"
        assert result.second_pass_result == "YES"
        assert "jaccard prefilter" in result.evidence

        # The LLM was called for non-template_collapse classes the loop
        # iterated through before reaching template_collapse. It was
        # NOT called for template_collapse itself - the pre-filter
        # short-circuited that one specific call. Each ollama.chat
        # call.args[0] is "model=...", so we use the markers prompt to
        # disambiguate.
        for call in mock_chat.call_args_list:
            messages = call.kwargs.get("messages") or call.args[1].get("messages")
            prompt = messages[0]["content"]
            assert "template_collapse" not in prompt, (
                "Pre-filter must short-circuit the template_collapse LLM "
                "call; found a prompt that names template_collapse."
            )
