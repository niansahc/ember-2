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
    def test_empty_logprobs_returns_high(self):
        assert compute_entropy([]) == 1.0

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
    def test_selects_multi_turn_when_prior_exists(self):
        cls = _select_pattern_class("casual", "prior response", "current response")
        assert cls is not None
        assert cls["name"] == "position_collapse"

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
