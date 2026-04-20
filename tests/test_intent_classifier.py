"""tests/test_intent_classifier.py

Unit tests for the three-tier intent classifier (ADR-034).

Stage 1 tests cover: definite internet signals, the compound first-person
guard, and escalation when no signal matches.

Stage 2 tests cover: lazy example-embedding cache, cosine similarity,
confidence threshold, graceful escalation when the embedder fails.

Stage 3 tests are added in a later commit on the same branch.
"""

from __future__ import annotations

import pytest

import src.llm.intent_classifier as intent_classifier
from src.llm.intent_classifier import (
    _cosine_similarity,
    _stage1_classify,
    _stage2_classify,
    classify_intent,
)


@pytest.fixture(autouse=True)
def reset_stage2_cache():
    """Ensure the Stage 2 example-embedding cache is fresh for each test.

    The cache is process-global; leaving it set between tests leaks mock
    state into unrelated cases.
    """
    intent_classifier._example_embeddings = None
    yield
    intent_classifier._example_embeddings = None


class TestStage1DefiniteInternetSignals:
    """Stage 1 routes clear external-world queries to needs_internet."""

    @pytest.mark.parametrize(
        "query",
        [
            "what's the weather today",
            "forecast for this weekend",
            "current temperature in Richmond",
            "bitcoin price right now",
            "stock price of NVDA",
            "crypto price today",
            "today's news",
            "current headlines",
            "latest updates on the election",
            "live score of the game",
            "standings after last night's match",
            "who won the championship",
        ],
    )
    def test_external_queries_route_to_needs_internet(self, query):
        assert _stage1_classify(query) == "needs_internet"


class TestStage1CompoundGuardAnchorOverrides:
    """Signal + first-person + external anchor => internet still wins.

    The compound guard only blocks to vault when first-person is present
    AND no external-world anchor word is present. If an anchor word appears,
    the query is asking about the external thing even though it uses
    first-person framing.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "I'm checking today's news",
            "my stock price alerts fire often",
            "I mentioned the weather last week",
            "I've been watching live score updates",
            "my bitcoin price alerts keep firing",
            "I said today's news was grim",
        ],
    )
    def test_signal_with_anchor_routes_to_internet_despite_first_person(self, query):
        result = _stage1_classify(query)
        assert result == "needs_internet", (
            f"{query!r}: first-person with external anchor should route to "
            f"internet, got {result!r}"
        )


class TestStage1CompoundGuardBlocksToVault:
    """Signal + first-person + NO anchor => guard blocks to vault.

    These queries trigger a definite-internet signal pattern but lack any
    external-world anchor word. The compound guard treats them as personal
    framing of the signal word, not actual requests for external info.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "my latest forecast was off",
            "I mentioned the temperature readings",
            "my standings list is growing",
            "I've been checking my who won predictions",
        ],
    )
    def test_signal_without_anchor_blocks_to_vault(self, query):
        assert _stage1_classify(query) == "vault_answerable"


class TestStage1Escalation:
    """Queries with no definite signal escalate (return None)."""

    @pytest.mark.parametrize(
        "query",
        [
            "what am i working on",
            "what did i say yesterday",
            "remind me about my current focus",
            "how have I been feeling",
            "tell me about my projects",
            "what's on my plate this week",
            # ADR §Stage 1 example: first-person + "currently" but no signal
            "what is my doctor currently recommending for my condition",
            # ADR §Stage 1 example: first-person subject, external verification
            # need but no Stage 1 signal — will resolve at Stage 2 or 3.
            "I've been reading that inflation is rising, is that still true",
        ],
    )
    def test_no_signal_escalates(self, query):
        assert _stage1_classify(query) is None

    def test_empty_query_escalates(self):
        assert _stage1_classify("") is None


class TestClassifyIntentPublicAPI:
    """classify_intent() always returns a valid label and logs once."""

    def test_stage1_resolves_to_needs_internet(self):
        assert classify_intent("what's the weather today") == "needs_internet"

    def test_stage1_resolves_to_vault_answerable(self):
        assert (
            classify_intent("my standings list is growing")
            == "vault_answerable"
        )

    def test_empty_query_falls_back_to_safe_default(self):
        assert classify_intent("") == "vault_answerable"

    def test_stage1_log_line_emitted(self, caplog):
        with caplog.at_level("INFO", logger="ember.intent_classifier"):
            classify_intent("what's the weather today")
        matches = [r for r in caplog.records if "[INTENT_CLASSIFY]" in r.message]
        assert len(matches) == 1
        assert "stage=stage1" in matches[0].message
        assert "label=needs_internet" in matches[0].message
        assert "confidence=none" in matches[0].message

    def test_fallback_log_line_emitted_when_all_stages_escalate(
        self, caplog, monkeypatch
    ):
        """When Stage 1 escalates and Stage 2 can't reach a threshold,
        commit-2 behavior is to fall back to the safe default. Stage 3
        lands in the next commit.
        """
        # Force Stage 2 to escalate cleanly.
        _inject_stage2_cache(
            [
                ("needs_internet", [1.0, 0.0, 0.0]),
                ("vault_answerable", [0.0, 1.0, 0.0]),
            ]
        )
        monkeypatch.setattr(intent_classifier, "embed_text", lambda q: [0.3, 0.3, 0.9])

        with caplog.at_level("INFO", logger="ember.intent_classifier"):
            label = classify_intent("deliberately ambiguous prompt text here")
        matches = [r for r in caplog.records if "[INTENT_CLASSIFY]" in r.message]
        assert len(matches) == 1
        assert "stage=fallback" in matches[0].message
        assert "label=vault_answerable" in matches[0].message
        assert label == "vault_answerable"


class TestLogLineIsAsciiOnly:
    """CLAUDE.md rule 7: diagnostic logs must be ASCII-only on Windows cp1252."""

    def test_log_line_is_pure_ascii(self, caplog):
        with caplog.at_level("INFO", logger="ember.intent_classifier"):
            classify_intent("what's the weather today")
        for record in caplog.records:
            if "[INTENT_CLASSIFY]" in record.message:
                # Encode to ASCII must succeed without replacement chars.
                record.message.encode("ascii")


class TestAdrRequiredCasesStage1:
    """Cases from the kickoff that Stage 1 alone can resolve.

    Cases requiring Stages 2 or 3 are covered in their respective commits
    on this branch. This class documents which kickoff cases land at
    Stage 1 vs later stages.
    """

    # Stage 1 positive cases — covered by the weather/stock/news signal
    # patterns.
    def test_whats_the_weather_today(self):
        assert classify_intent("What's the weather today?") == "needs_internet"

    # Cases requiring Stages 2+ — these escalate at Stage 1 and are
    # expected to be resolved by Stage 2 or Stage 3.
    @pytest.mark.parametrize(
        "query",
        [
            "I'm currently working on a project",
            "I've been watching a show",
            "What are my latest projects?",
            "What did I say about the migration?",
            "What's the current price of Bitcoin?",
            "I've been reading that inflation is rising, is that still true",
        ],
    )
    def test_stage1_escalates_on_cases_needing_later_stages(self, query):
        assert _stage1_classify(query) is None


# ---------------------------------------------------------------------------
# Stage 2 tests
# ---------------------------------------------------------------------------

def _inject_stage2_cache(
    labels_and_vectors: list[tuple[str, list[float]]],
) -> None:
    """Bypass the lazy loader by injecting a minimal example cache."""
    intent_classifier._example_embeddings = labels_and_vectors


class TestCosineSimilarity:
    """Internal helper — sanity checks for the cosine kernel."""

    def test_parallel_vectors_return_one(self):
        assert _cosine_similarity([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)

    def test_orthogonal_vectors_return_zero(self):
        assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_returns_zero_without_crashing(self):
        assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0


class TestStage2HighConfidence:
    """Stage 2 returns the matching label when top-1 similarity >= 0.65."""

    def test_near_perfect_match_for_needs_internet_returns_label(self, monkeypatch):
        _inject_stage2_cache(
            [
                ("needs_internet", [1.0, 0.0]),
                ("vault_answerable", [0.0, 1.0]),
            ]
        )
        monkeypatch.setattr(intent_classifier, "embed_text", lambda q: [1.0, 0.0])
        label, confidence = _stage2_classify("bitcoin price right now")
        assert label == "needs_internet"
        assert confidence is not None and confidence > 0.99

    def test_near_perfect_match_for_vault_returns_label(self, monkeypatch):
        _inject_stage2_cache(
            [
                ("needs_internet", [1.0, 0.0]),
                ("vault_answerable", [0.0, 1.0]),
            ]
        )
        monkeypatch.setattr(intent_classifier, "embed_text", lambda q: [0.0, 1.0])
        label, confidence = _stage2_classify("my current projects")
        assert label == "vault_answerable"
        assert confidence is not None and confidence > 0.99


class TestStage2LowConfidenceEscalates:
    """Stage 2 returns (None, top_confidence) when below threshold."""

    def test_ambiguous_query_escalates(self, monkeypatch):
        # Use a vector whose top cosine similarity to either class is
        # below 0.65. [0.4, 0.9] has ~0.406 similarity to [1,0] and
        # ~0.913 to [0,1] — so swap to a case where neither is close.
        _inject_stage2_cache(
            [
                ("needs_internet", [1.0, 0.0, 0.0]),
                ("vault_answerable", [0.0, 1.0, 0.0]),
            ]
        )
        # Roughly equidistant-but-weak: mostly aligned with a neutral axis.
        monkeypatch.setattr(intent_classifier, "embed_text", lambda q: [0.3, 0.3, 0.9])
        label, confidence = _stage2_classify("something truly ambiguous")
        assert label is None
        assert confidence is not None
        assert confidence < 0.65


class TestStage2ResilientFallback:
    """Ollama failures must escalate gracefully, never raise."""

    def test_embed_raises_escalates_cleanly(self, monkeypatch):
        _inject_stage2_cache(
            [
                ("needs_internet", [1.0, 0.0]),
                ("vault_answerable", [0.0, 1.0]),
            ]
        )

        def boom(query):
            raise ConnectionError("Ollama unreachable")

        monkeypatch.setattr(intent_classifier, "embed_text", boom)
        label, confidence = _stage2_classify("any query")
        assert label is None
        assert confidence is None

    def test_cache_load_failure_escalates_cleanly(self, monkeypatch):
        # Force the loader to see a broken embed_texts path on first call.
        def boom(texts):
            raise ConnectionError("Ollama unreachable")

        monkeypatch.setattr(intent_classifier, "embed_texts", boom)
        label, confidence = _stage2_classify("any query")
        assert label is None
        assert confidence is None

    def test_empty_query_returns_none(self):
        label, confidence = _stage2_classify("")
        assert label is None
        assert confidence is None


class TestStage2CacheBehavior:
    """The example-embedding cache is populated once and reused."""

    def test_cache_populated_on_first_call_only(self, monkeypatch):
        call_count = {"n": 0}

        def counted_embed_texts(texts):
            call_count["n"] += 1
            return [[1.0, 0.0]] * len(texts)

        monkeypatch.setattr(intent_classifier, "embed_texts", counted_embed_texts)
        monkeypatch.setattr(intent_classifier, "embed_text", lambda q: [1.0, 0.0])

        # Two back-to-back classifications must only load the cache once.
        _stage2_classify("first query")
        _stage2_classify("second query")
        assert call_count["n"] == 1


class TestClassifyIntentStage2Flow:
    """Stage 2 result flows through classify_intent's top-level log line."""

    def test_stage2_log_line_includes_confidence(self, caplog, monkeypatch):
        _inject_stage2_cache(
            [
                ("needs_internet", [1.0, 0.0]),
                ("vault_answerable", [0.0, 1.0]),
            ]
        )
        monkeypatch.setattr(intent_classifier, "embed_text", lambda q: [1.0, 0.0])
        # Use a query that escapes Stage 1 so Stage 2 actually runs.
        query = "tell me what's going on with the project"
        with caplog.at_level("INFO", logger="ember.intent_classifier"):
            label = classify_intent(query)
        matches = [r for r in caplog.records if "[INTENT_CLASSIFY]" in r.message]
        assert len(matches) == 1
        msg = matches[0].message
        assert "stage=stage2" in msg
        assert "label=needs_internet" in msg
        assert "confidence=" in msg and "confidence=none" not in msg
        assert label == "needs_internet"

    def test_stage2_escalation_falls_back_to_safe_default(self, caplog, monkeypatch):
        _inject_stage2_cache(
            [
                ("needs_internet", [1.0, 0.0, 0.0]),
                ("vault_answerable", [0.0, 1.0, 0.0]),
            ]
        )
        monkeypatch.setattr(intent_classifier, "embed_text", lambda q: [0.3, 0.3, 0.9])
        query = "give me a completely ambiguous prompt please"
        with caplog.at_level("INFO", logger="ember.intent_classifier"):
            label = classify_intent(query)
        # Commit 2 behavior: when Stage 2 escalates, Stage 3 is not yet
        # implemented so classify_intent falls back to the safe default.
        assert label == "vault_answerable"
        matches = [r for r in caplog.records if "[INTENT_CLASSIFY]" in r.message]
        assert len(matches) == 1
        assert "stage=fallback" in matches[0].message
