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
)


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
