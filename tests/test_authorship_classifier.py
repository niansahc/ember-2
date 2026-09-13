"""
tests/test_authorship_classifier.py

Unit tests for src/memory/authorship.py::classify_authorship, the live
write-path authorship classifier (Recalling Too Well Phase 1, item 2).
"""

from src.memory.authorship import classify_authorship


class TestProfile:
    def test_profile_is_always_first_person(self):
        assert classify_authorship("profile", "onboarding", {}) == "first_person"

    def test_profile_ignores_source(self):
        assert classify_authorship("profile", "api", {}) == "first_person"


class TestJournal:
    def test_journal_is_always_first_person(self):
        assert classify_authorship("journal", "api", {}) == "first_person"


class TestReflection:
    def test_reflection_engine_is_first_person(self):
        assert classify_authorship("reflection", "reflection_engine", {}) == "first_person"

    def test_session_reflection_is_first_person(self):
        assert classify_authorship("reflection", "session_reflection", {}) == "first_person"

    def test_unrecognized_reflection_source_is_unknown(self):
        assert classify_authorship("reflection", "some_other_source", {}) == "unknown"

    def test_reflection_none_source_is_unknown(self):
        assert classify_authorship("reflection", None, {}) == "unknown"


class TestConversation:
    def test_user_role_is_first_person(self):
        assert classify_authorship("conversation", "chat", {"role": "user"}) == "first_person"

    def test_assistant_role_is_mixed(self):
        assert classify_authorship("conversation", "chat", {"role": "assistant"}) == "mixed"

    def test_missing_role_is_unknown(self):
        assert classify_authorship("conversation", "chat", {}) == "unknown"

    def test_none_metadata_is_unknown(self):
        assert classify_authorship("conversation", "chat", None) == "unknown"


class TestOtherTypes:
    def test_unrecognized_memory_type_is_unknown(self):
        assert classify_authorship("deviation", "deviation_detector", {}) == "unknown"

    def test_ingested_is_unknown(self):
        # Ingested/imported content is classified separately by
        # scripts/rebuild_authorship_index.py -- the live path never
        # writes third_party.
        assert classify_authorship("ingested", "pdf", {}) == "unknown"
