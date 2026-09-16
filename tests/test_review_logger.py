"""
tests/test_review_logger.py

Regression tests for #151: SafetyReviewLogger must never write verbatim
user_message/draft_response/final_response text, and must never leak
response text through the critique payload's issues_found field either
(a second, independent channel -- review_service.py's MVR critique path
quotes a failing sentence from the draft response into each issue label).

All fixture text below is synthetic (CLAUDE.md vault privacy rule for
test fixtures) and deliberately distinctive so a substring-search
regression test is meaningful.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from src.context.models import ContextPacket
from src.safety.models import SafetyCritique, SafetyReviewResult, SafetyTriggerResult
from src.safety.review_logger import SafetyReviewLogger

USER_MESSAGE = "zzz-marker-user-message-canary-9f2a"
DRAFT_RESPONSE = "zzz-marker-draft-response-canary-7c1e"
FINAL_RESPONSE = "zzz-marker-final-response-canary-3d8b"
QUOTED_SENTENCE = "zzz-marker-quoted-sentence-canary-5e0f"


@pytest.fixture
def logger(tmp_path):
    return SafetyReviewLogger(log_dir=tmp_path)


def _packet(user_message: str = USER_MESSAGE) -> ContextPacket:
    return ContextPacket(
        user_message=user_message,
        memory_items=[object(), object()],
        reflection_items=[object()],
    )


def _trigger(triggered: bool = True) -> SafetyTriggerResult:
    return SafetyTriggerResult(
        triggered=triggered,
        triggered_by=["dual_use"],
        notes=[],
    )


def _review(critique: SafetyCritique | None, outcome: str = "revise") -> SafetyReviewResult:
    return SafetyReviewResult(
        triggered=True,
        outcome=outcome,
        rules=["non_harm"],
        critique=critique,
        reviewed_text=FINAL_RESPONSE,
        refusal_message=None,
    )


class TestNoVerbatimText:
    def test_log_file_does_not_contain_user_message(self, logger):
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(None))
        raw = path.read_text(encoding="utf-8")
        assert USER_MESSAGE not in raw

    def test_log_file_does_not_contain_draft_response(self, logger):
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(None))
        raw = path.read_text(encoding="utf-8")
        assert DRAFT_RESPONSE not in raw

    def test_log_file_does_not_contain_final_response(self, logger):
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(None))
        raw = path.read_text(encoding="utf-8")
        assert FINAL_RESPONSE not in raw

    def test_log_file_does_not_contain_quoted_critique_sentence(self, logger):
        """The second leak vector: review_service.py's MVR critique path
        can compose issues_found entries as f"{criterion}: {sentence}",
        where sentence is a verbatim excerpt of the draft response."""
        critique = SafetyCritique(
            issues_found=[f"position_collapse: {QUOTED_SENTENCE}"],
            severity="medium",
            suggested_changes=["Revise the response to address the position_collapse concern."],
            triggered_rules=["position_collapse"],
        )
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(critique))
        raw = path.read_text(encoding="utf-8")
        assert QUOTED_SENTENCE not in raw
        assert "issues_found" not in raw
        assert "suggested_changes" not in raw

    def test_no_response_related_keys_survive_round_trip(self, logger):
        critique = SafetyCritique(
            issues_found=["x: y"], severity="low",
            suggested_changes=["z"], triggered_rules=["sycophancy"],
        )
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(critique))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert "user_message" not in data
        assert "draft_response" not in data
        assert "final_response" not in data
        assert "issues_found" not in data.get("critique", {})
        assert "suggested_changes" not in data.get("critique", {})


class TestLengthsAndHashes:
    def test_lengths_are_correct(self, logger):
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(None))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["user_message_length"] == len(USER_MESSAGE)
        assert data["draft_response_length"] == len(DRAFT_RESPONSE)
        # outcome defaults to "revise" with reviewed_text=FINAL_RESPONSE
        assert data["final_response_length"] == len(FINAL_RESPONSE)

    def test_hashes_are_correct_sha256(self, logger):
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(None))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["user_message_sha256"] == hashlib.sha256(
            USER_MESSAGE.encode("utf-8")
        ).hexdigest()
        assert data["draft_response_sha256"] == hashlib.sha256(
            DRAFT_RESPONSE.encode("utf-8")
        ).hexdigest()

    def test_identical_text_produces_identical_hash(self, logger):
        """The point of the hash: exact-repeat detection across entries
        without ever storing or re-reading the content (repetition-loop
        diagnosis, docs/KNOWN_ISSUES.md B-LOOP-001)."""
        p1 = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(None))
        p2 = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(None))
        d1 = json.loads(p1.read_text(encoding="utf-8"))
        d2 = json.loads(p2.read_text(encoding="utf-8"))
        assert d1["draft_response_sha256"] == d2["draft_response_sha256"]


class TestCritiquePayload:
    def test_critique_none_when_no_critique(self, logger):
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(None))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["critique"] is None

    def test_critique_carries_severity_rules_and_issue_count(self, logger):
        critique = SafetyCritique(
            issues_found=["a: 1", "b: 2", "c: 3"],
            severity="high",
            suggested_changes=["x"],
            triggered_rules=["non_harm", "position_collapse"],
        )
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(critique))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["critique"]["severity"] == "high"
        assert data["critique"]["triggered_rules"] == ["non_harm", "position_collapse"]
        assert data["critique"]["issue_count"] == 3


class TestSessionId:
    def test_session_id_written_when_provided(self, logger):
        path = logger.log(
            _packet(), DRAFT_RESPONSE, _trigger(), _review(None),
            session_id="sess_test_001",
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["session_id"] == "sess_test_001"

    def test_session_id_none_when_not_provided(self, logger):
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(None))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["session_id"] is None


class TestUnchangedSections:
    """Regression guard: the parts that were already content-free must
    stay exactly as they were."""

    def test_trigger_section_shape(self, logger):
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(None))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["trigger"] == {
            "triggered": True,
            "triggered_by": ["dual_use"],
            "notes": [],
        }

    def test_review_section_is_log_payload(self, logger):
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(None))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["review"] == {
            "triggered": True,
            "outcome": "revise",
            "rules": ["non_harm"],
        }

    def test_metadata_section_counts_items(self, logger):
        path = logger.log(_packet(), DRAFT_RESPONSE, _trigger(), _review(None))
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["metadata"] == {"memory_count": 2, "reflection_count": 1}
