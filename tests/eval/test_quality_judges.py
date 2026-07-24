"""
tests/eval/test_quality_judges.py

Unit tests for the judge response PARSERS (pure, Tier-1 safe). The actual
Anthropic calls (score_claims / score_turn) import anthropic lazily and are
exercised only in the live release-gate run, not here.
"""

from tests.eval.quality_judges import (
    parse_claim_verdicts,
    parse_turn_scores,
    NEUTRAL_TURN_SCORE,
    sonnet_judge_model,
    haiku_judge_model,
    DEFAULT_SONNET_JUDGE_MODEL,
    DEFAULT_HAIKU_JUDGE_MODEL,
)


def test_sonnet_judge_model_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("EMBER_EVAL_JUDGE_SONNET_MODEL", raising=False)
    assert sonnet_judge_model() == DEFAULT_SONNET_JUDGE_MODEL
    # The default must be a model id that broadly-provisioned keys can reach.
    assert DEFAULT_SONNET_JUDGE_MODEL == "claude-sonnet-4-5-20250929"


def test_sonnet_judge_model_env_override(monkeypatch):
    monkeypatch.setenv("EMBER_EVAL_JUDGE_SONNET_MODEL", "claude-sonnet-custom")
    assert sonnet_judge_model() == "claude-sonnet-custom"


def test_haiku_judge_model_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("EMBER_EVAL_JUDGE_HAIKU_MODEL", raising=False)
    assert haiku_judge_model() == DEFAULT_HAIKU_JUDGE_MODEL
    assert DEFAULT_HAIKU_JUDGE_MODEL == "claude-haiku-4-5-20251001"


def test_haiku_judge_model_env_override(monkeypatch):
    monkeypatch.setenv("EMBER_EVAL_JUDGE_HAIKU_MODEL", "claude-haiku-custom")
    assert haiku_judge_model() == "claude-haiku-custom"


def test_score_claims_retries_transient_failure(monkeypatch):
    # Proves the grounding judge is wrapped in retry_call: a client that fails
    # once (e.g. a 429) then succeeds must still yield the parsed verdict, not
    # the error sentinel. A fake anthropic module is injected so no real SDK /
    # network is touched (CI-safe); time.sleep is stubbed so the backoff is
    # instant.
    import sys
    import types
    from unittest.mock import MagicMock

    calls = {"n": 0}

    class _FakeMessages:
        def create(self, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("429 rate limited")
            msg = MagicMock()
            msg.content = [MagicMock(text='{"claims": [{"claim": "x", "supported": true}]}')]
            return msg

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            self.messages = _FakeMessages()

    fake_mod = types.ModuleType("anthropic")
    fake_mod.Anthropic = _FakeAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_mod)
    monkeypatch.setattr("time.sleep", lambda _s: None)

    from tests.eval.quality_judges import score_claims
    out = score_claims("Ember's response", ["a retrieved record"])

    assert out == [{"claim": "x", "supported": True}]
    assert calls["n"] == 2  # failed once, retried, succeeded


def test_parse_claim_verdicts_happy_path():
    text = (
        '{"claims": [{"claim": "deadline is Friday", "supported": true}, '
        '{"claim": "manager is Sarah", "supported": false}]}'
    )
    out = parse_claim_verdicts(text)
    assert out == [
        {"claim": "deadline is Friday", "supported": True},
        {"claim": "manager is Sarah", "supported": False},
    ]


def test_parse_claim_verdicts_accepts_yes_no_strings():
    text = '{"claims": [{"claim": "x", "supported": "YES"}, {"claim": "y", "supported": "NO"}]}'
    out = parse_claim_verdicts(text)
    assert out[0]["supported"] is True
    assert out[1]["supported"] is False


def test_parse_claim_verdicts_strips_code_fences():
    text = '```json\n{"claims": [{"claim": "x", "supported": true}]}\n```'
    assert parse_claim_verdicts(text) == [{"claim": "x", "supported": True}]


def test_parse_claim_verdicts_bad_json_returns_empty():
    # A response with no parseable claims is treated as "no claims" - the caller
    # surfaces the raw parse failure separately; it never silently fabricates.
    assert parse_claim_verdicts("not json at all") == []


def test_parse_turn_scores_happy_path():
    text = '{"scores": {"register": 4, "honesty": 3, "self_narrative": 2}}'
    out = parse_turn_scores(text, ["register", "honesty", "self_narrative"])
    assert out == {"register": 4.0, "honesty": 3.0, "self_narrative": 2.0}


def test_parse_turn_scores_clamps_out_of_range():
    text = '{"scores": {"register": 9, "honesty": 0}}'
    out = parse_turn_scores(text, ["register", "honesty"])
    assert out["register"] == 4.0
    assert out["honesty"] == 1.0


def test_parse_turn_scores_missing_dim_defaults_neutral():
    # A missing/unparseable dimension defaults to a constant so a judge blip does
    # not fabricate drift (a constant adds no artificial window delta or slope).
    text = '{"scores": {"register": 4}}'
    out = parse_turn_scores(text, ["register", "honesty"])
    assert out["register"] == 4.0
    assert out["honesty"] == NEUTRAL_TURN_SCORE


def test_parse_expectation_verdict():
    from tests.eval.quality_judges import parse_expectation_verdict
    ok = parse_expectation_verdict('{"passed": true, "score": 4, "reason": "meets it"}')
    assert ok == {"passed": True, "score": 4.0, "error": False}
    clamped = parse_expectation_verdict('{"passed": false, "score": 9}')
    assert clamped["passed"] is False and clamped["score"] == 4.0 and clamped["error"] is False
    err = parse_expectation_verdict("not json at all")
    assert err["error"] is True and err["passed"] is False
