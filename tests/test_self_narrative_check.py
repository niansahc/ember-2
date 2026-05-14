"""tests/test_self_narrative_check.py

Tests for src/safety/self_narrative_check.py (B3 fix).

Class-based detector that flags any sentence where Ember asserts a
present-tense capability or state about her own subsystems. Audit-only
- never blocks or rewrites. Pure function, no I/O.

Background:
  - B3 UAT 2026-05-11 surfaced a self-narrative hallucination
    ("search functionality is broken by design"). No detector caught
    it. Diagnosis at docs/audits/b3_self_narrative_diagnosis.md
    (commit f8da0f2) selected Option C (class-based detection).
  - Grill-with-docs session locked the design across five Qs.

See plan file C:\\Users\\nians\\.claude\\plans\\glistening-swimming-octopus.md.
"""

from __future__ import annotations


class TestSelfNarrativeCheckHelper:
    """Pure-function unit tests on `check(response_text)`."""

    def test_empty_response_returns_no_flags(self):
        from src.safety.self_narrative_check import check
        assert check("") == (0, [])

    def test_b3_actual_phrasing_caught(self):
        """B3 regression: the verbatim UAT-style surface form must
        flag with index 0 (single-sentence response)."""
        from src.safety.self_narrative_check import check
        text = "The search functionality is broken by design."
        count, indices = check(text)
        assert count == 1
        assert indices == [0]
        assert all(isinstance(i, int) for i in indices)

    def test_subsystem_subject_with_closed_verb_caught(self):
        """Non-I arm: 'the vault has X' fires on subject 'the vault' +
        verb 'has' from the closed set."""
        from src.safety.self_narrative_check import check
        text = "The vault has 12 records from this week."
        count, indices = check(text)
        assert count == 1
        assert indices == [0]

    def test_possessive_subject_caught(self):
        """Possessive form 'my X' for any subsystem noun flags on the
        non-I arm. 'my memory' is a system reference."""
        from src.safety.self_narrative_check import check
        text = "My memory is empty right now."
        count, indices = check(text)
        assert count == 1
        assert indices == [0]

    def test_i_arm_with_subsystem_noun_caught(self):
        """I-arm requires co-occurrence of 'I' + closed verb + subsystem
        noun. 'I cannot access the vault' satisfies all three."""
        from src.safety.self_narrative_check import check
        text = "I cannot access the vault."
        count, indices = check(text)
        assert count == 1
        assert indices == [0]

    def test_i_arm_without_subsystem_noun_not_caught(self):
        """Legitimate first-person utterance with no subsystem noun
        must not flag. 'I am here to help' is a greeting, not a
        system claim."""
        from src.safety.self_narrative_check import check
        count, indices = check("I am here to help.")
        assert count == 0
        assert indices == []

    def test_opinion_verb_not_caught(self):
        """Opinion-framing verbs ('think', 'notice', 'wonder', etc.)
        are not in the closed verb set, so 'I think the migration is
        tricky' does not match the I arm. The 'the migration'
        subject is not in the subsystem list either, so the non-I arm
        also does not match. No flag."""
        from src.safety.self_narrative_check import check
        count, indices = check("I think the migration is tricky.")
        assert count == 0
        assert indices == []

    def test_definite_article_required(self):
        """Subsystem nouns without 'the' or 'my' must not match.

        - 'Search returns five results' (bare 'search', verb 'returns'
          not in closed set anyway): no flag.
        - 'The search returns five results' (verb 'returns' not in set):
          no flag.
        - 'The search is operational' (verb 'is' in set + 'the search'
          subject): flags.
        """
        from src.safety.self_narrative_check import check
        assert check("Search returns five results.") == (0, [])
        assert check("The search returns five results.") == (0, [])
        count, indices = check("The search is operational.")
        assert count == 1
        assert indices == [0]

    def test_bare_noun_miss_class_documented(self):
        """B-NARR-001 miss class: bare subsystem noun without article
        or possessive does not flag. 'I cannot access memory' lacks
        'the memory' / 'my memory' so the I-arm co-occurrence does not
        fire. Documented as deliberate v0.18.0 boundary; v0.19.0
        capabilities corpus upgrade closes the gap."""
        from src.safety.self_narrative_check import check
        count, indices = check("I cannot access memory.")
        assert count == 0
        assert indices == []

    def test_multi_sentence_response_returns_correct_indices(self):
        """Indices are 0-indexed positions within the naive sentence
        split. A four-sentence response where sentences 1 and 3 contain
        self-narrative claims yields (2, [1, 3])."""
        from src.safety.self_narrative_check import check
        text = (
            "The migration handles records created before the cutoff. "
            "The vault is read-only this morning. "
            "Let me think about the second part. "
            "The search functionality is broken by design."
        )
        count, indices = check(text)
        assert count == 2
        assert indices == [1, 3]
        # Pin: sentence_indices is list[int], not list[str] or other.
        assert all(isinstance(i, int) for i in indices)


class TestSelfNarrativeIntegration:
    """Request-handler integration tests: check() is invoked, and the
    result lands in the safety_reviews log per Option B lifecycle."""

    def test_check_invoked_by_request_handler(self):
        """Verification per Q4: assert the handler calls check() once
        on a normal chat completion. Mock the check function so the
        assertion is on the call count, not on log shape."""
        from unittest.mock import patch
        from fastapi.testclient import TestClient

        payload = {
            "model": "ember",
            "stream": False,
            "messages": [{"role": "user", "content": "hello there"}],
        }

        with patch("src.api.main.get_ember_api_key", return_value=None):
            from src.api.main import app
            with patch(
                "src.safety.self_narrative_check.check",
                return_value=(0, []),
            ) as mock_check, patch(
                "src.api.openai_adapter.context_service",
            ) as _ctx, patch(
                "src.api.openai_adapter.llm_adapter",
            ) as _llm, patch(
                "src.api.openai_adapter.write_memory",
            ), patch(
                "src.api.openai_adapter._background_state_extraction",
            ), patch(
                "src.api.openai_adapter._detect_and_write_commitment",
            ), patch(
                "src.api.openai_adapter._detect_task_in_response",
            ), patch(
                "src.api.openai_adapter.onboarding_service",
            ) as _onb, patch(
                "src.api.openai_adapter._ensure_session",
            ):
                _onb.is_active.return_value = False
                from src.context.models import ContextPacket
                _ctx.build_context.return_value = ContextPacket(
                    user_message="hello there", web_items=[],
                )
                _llm.generate_response.return_value = (
                    "Hi. The vault is empty right now."
                )

                client = TestClient(app)
                resp = client.post("/v1/chat/completions", json=payload)

                assert resp.status_code == 200
                assert mock_check.call_count >= 1, (
                    f"self_narrative_check.check() must be invoked by "
                    f"the request handler; got call_count="
                    f"{mock_check.call_count}"
                )

    def test_flagged_response_logs_via_log_self_narrative_outcome(self):
        """When the response contains a self-narrative claim, the
        request handler must call log_self_narrative_outcome with
        flag_count > 0. The log function decides whether to write a
        file (it skips on count=0 per Option B lifecycle)."""
        from unittest.mock import patch
        from fastapi.testclient import TestClient

        payload = {
            "model": "ember",
            "stream": False,
            "messages": [{"role": "user", "content": "Is search working?"}],
        }

        flagged_reply = "The search functionality is broken by design."

        with patch("src.api.main.get_ember_api_key", return_value=None):
            from src.api.main import app
            with patch(
                "src.safety.self_narrative_check.log_self_narrative_outcome",
            ) as mock_log, patch(
                "src.api.openai_adapter.context_service",
            ) as _ctx, patch(
                "src.api.openai_adapter.llm_adapter",
            ) as _llm, patch(
                "src.api.openai_adapter.write_memory",
            ), patch(
                "src.api.openai_adapter._background_state_extraction",
            ), patch(
                "src.api.openai_adapter._detect_and_write_commitment",
            ), patch(
                "src.api.openai_adapter._detect_task_in_response",
            ), patch(
                "src.api.openai_adapter.onboarding_service",
            ) as _onb, patch(
                "src.api.openai_adapter._ensure_session",
            ):
                _onb.is_active.return_value = False
                from src.context.models import ContextPacket
                _ctx.build_context.return_value = ContextPacket(
                    user_message="Is search working?", web_items=[],
                )
                _llm.generate_response.return_value = flagged_reply

                client = TestClient(app)
                resp = client.post("/v1/chat/completions", json=payload)

                assert resp.status_code == 200
                assert mock_log.call_count >= 1
                # First positional arg is flag_count.
                call = mock_log.call_args_list[0]
                flag_count = call.args[0] if call.args else call.kwargs.get(
                    "flag_count", 0,
                )
                assert flag_count >= 1, (
                    f"flagged response should produce flag_count >= 1; "
                    f"got {flag_count}"
                )
                # Privacy: response text must not appear in the call args.
                for arg in list(call.args) + list(call.kwargs.values()):
                    assert flagged_reply not in repr(arg), (
                        f"Response text leaked into log call args: {arg!r}"
                    )

    def test_clean_response_logs_with_zero_flag_count(self):
        """When the response has no self-narrative claims, the request
        handler still calls log_self_narrative_outcome - but with
        flag_count = 0. The log function then skips the file write per
        Option B. This pins the contract: check() is always invoked,
        the log function gates on count."""
        from unittest.mock import patch
        from fastapi.testclient import TestClient

        payload = {
            "model": "ember",
            "stream": False,
            "messages": [{"role": "user", "content": "what time is it"}],
        }

        clean_reply = "I do not have a clock available right now."

        with patch("src.api.main.get_ember_api_key", return_value=None):
            from src.api.main import app
            with patch(
                "src.safety.self_narrative_check.log_self_narrative_outcome",
            ) as mock_log, patch(
                "src.api.openai_adapter.context_service",
            ) as _ctx, patch(
                "src.api.openai_adapter.llm_adapter",
            ) as _llm, patch(
                "src.api.openai_adapter.write_memory",
            ), patch(
                "src.api.openai_adapter._background_state_extraction",
            ), patch(
                "src.api.openai_adapter._detect_and_write_commitment",
            ), patch(
                "src.api.openai_adapter._detect_task_in_response",
            ), patch(
                "src.api.openai_adapter.onboarding_service",
            ) as _onb, patch(
                "src.api.openai_adapter._ensure_session",
            ):
                _onb.is_active.return_value = False
                from src.context.models import ContextPacket
                _ctx.build_context.return_value = ContextPacket(
                    user_message="what time is it", web_items=[],
                )
                _llm.generate_response.return_value = clean_reply

                client = TestClient(app)
                resp = client.post("/v1/chat/completions", json=payload)

                assert resp.status_code == 200
                assert mock_log.call_count >= 1
                call = mock_log.call_args_list[0]
                flag_count = call.args[0] if call.args else call.kwargs.get(
                    "flag_count", 0,
                )
                assert flag_count == 0, (
                    f"clean response should produce flag_count = 0; "
                    f"got {flag_count}"
                )
