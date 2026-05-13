"""tests/test_intent_classifier.py

Unit tests for the three-tier intent classifier (ADR-034).

Stage 1 tests cover: definite internet signals, the compound first-person
guard, and escalation when no signal matches.

Stage 2 tests cover: lazy example-embedding cache, cosine similarity,
confidence threshold, graceful escalation when the embedder fails.

Stage 3 tests cover: LLM happy path, JSON parse failure, unknown-label
rejection, hard timeout fallback, log-line tagging.
"""

from __future__ import annotations

import concurrent.futures
import json

import httpx
import numpy as np
import pytest

import src.llm.intent_classifier as intent_classifier
from src.llm.classifier_examples import EXAMPLES
from src.llm.intent_classifier import (
    NEEDS_INTERNET,
    VAULT_ANSWERABLE,
    _stage1_classify,
    _stage2_classify,
    _stage3_classify_with_timeout,
    _stage3_llm_call,
    classify_intent,
)


def _ollama_reachable() -> bool:
    """Return True if a local Ollama instance responds to /api/version.

    Stage 2 (nomic-embed-text) and Stage 3 (qwen3:8b) both require a
    running Ollama. Tests that exercise the live classifier path skip
    when it is not reachable (e.g. CI without an Ollama service).
    """
    try:
        httpx.get("http://localhost:11434/api/version", timeout=1.0)
        return True
    except Exception:
        return False


_NEEDS_OLLAMA = pytest.mark.skipif(
    not _ollama_reachable(),
    reason="stage 2/3 classifier needs Ollama (nomic-embed-text + qwen3:8b)",
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


class TestStage1ConversationalAcks:
    """Bare conversational acknowledgments short-circuit to vault_answerable.

    Background: single-phrase acks like 'thank you' / 'okay' were reaching the
    Stage 3 LLM with no vault context and getting misclassified as
    needs_internet. Stage 1 short-circuits the entire normalized message —
    NOT substring matching — so phrases that merely contain an ack-word as a
    prefix still flow through the cascade.
    """

    @pytest.mark.parametrize(
        "query",
        [
            "thank you",
            "thanks",
            "okay",
            "ok",
            "got it",
            "you're welcome",
            "you're right",
            "i appreciate it",
            "no worries",
            "fair enough",
            "noted",
            "sounds good",
            "makes sense",
            "understood",
        ],
    )
    def test_conversational_acks_route_to_vault_answerable(self, query):
        assert _stage1_classify(query) == "vault_answerable"

    @pytest.mark.parametrize(
        "query",
        [
            "Thank you.",
            "Thanks!",
            "Okay?",
            "Got it!!",
            "thank you, ",
        ],
    )
    def test_acks_normalized_for_punctuation_and_case(self, query):
        """Trailing punctuation and case must not defeat the short-circuit."""
        assert _stage1_classify(query) == "vault_answerable"

    @pytest.mark.parametrize(
        "query",
        [
            "thanks for the news",
            "okay but what's the weather",
            "got it. what about today's headlines",
            "thank you for explaining bitcoin price",
        ],
    )
    def test_acks_embedded_in_real_queries_do_not_short_circuit(self, query):
        """Substring matching would false-positive on these — the short-circuit
        must require the entire normalized message to be in the ack set.
        These queries should fall through Stage 1 (return None to escalate)
        OR match a definite-internet signal in the same message."""
        result = _stage1_classify(query)
        # Either escalates (None) or matches the internet signal in the
        # remainder of the message — but must NOT short-circuit to vault on
        # the ack alone.
        if result == "vault_answerable":
            pytest.fail(
                f"{query!r} was incorrectly short-circuited to vault_answerable "
                f"despite containing additional content beyond an ack."
            )


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

    # The previous commit's "fallback" log path no longer exists — Stage 3
    # absorbs the escalation space. The Stage 3 tests below
    # (TestClassifyIntentStage3Flow) cover the equivalent cases
    # deterministically by mocking _stage3_classify_with_timeout.


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
    """Bypass the lazy loader by injecting a minimal pre-normalized cache.

    Accepts the readable (label, vector) test form and stacks + unit-
    normalizes the vectors to match the production cache layout.
    """
    labels = [label for label, _ in labels_and_vectors]
    matrix = np.asarray(
        [vec for _, vec in labels_and_vectors], dtype=np.float32
    )
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    intent_classifier._example_embeddings = (labels, matrix / norms)


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

    def test_stage3_is_reached_when_stages_1_and_2_escalate(
        self, caplog, monkeypatch
    ):
        _inject_stage2_cache(
            [
                ("needs_internet", [1.0, 0.0, 0.0]),
                ("vault_answerable", [0.0, 1.0, 0.0]),
            ]
        )
        monkeypatch.setattr(intent_classifier, "embed_text", lambda q: [0.3, 0.3, 0.9])
        # Force Stage 3 to decisively return needs_internet so the log
        # line tags stage=stage3, not fallback or stage2.
        monkeypatch.setattr(
            intent_classifier,
            "_stage3_classify_with_timeout",
            lambda q: ("needs_internet", False),
        )
        query = "genuinely ambiguous external question with no keyword signals"
        with caplog.at_level("INFO", logger="ember.intent_classifier"):
            label = classify_intent(query)
        assert label == "needs_internet"
        matches = [r for r in caplog.records if "[INTENT_CLASSIFY]" in r.message]
        assert len(matches) == 1
        assert "stage=stage3" in matches[0].message
        assert "label=needs_internet" in matches[0].message


# ---------------------------------------------------------------------------
# Stage 3 tests
# ---------------------------------------------------------------------------

class FakeOllama:
    """Drop-in replacement for the ollama module at test time."""

    def __init__(self, content: str | None = None, exc: Exception | None = None):
        self._content = content
        self._exc = exc
        self.chat_calls: list[dict] = []

    def chat(self, **kwargs):
        self.chat_calls.append(kwargs)
        if self._exc is not None:
            raise self._exc
        return {"message": {"content": self._content}}


class TestStage3LlmCall:
    """The raw Stage 3 LLM call — no timeout wrapping."""

    def test_returns_needs_internet_on_valid_json(self, monkeypatch):
        fake = FakeOllama(content=json.dumps({"label": "needs_internet"}))
        monkeypatch.setattr(intent_classifier, "ollama", fake)
        assert _stage3_llm_call("some query") == "needs_internet"

    def test_returns_vault_answerable_on_valid_json(self, monkeypatch):
        fake = FakeOllama(content=json.dumps({"label": "vault_answerable"}))
        monkeypatch.setattr(intent_classifier, "ollama", fake)
        assert _stage3_llm_call("some query") == "vault_answerable"

    def test_non_thinking_mode_is_sent_to_ollama(self, monkeypatch):
        fake = FakeOllama(content=json.dumps({"label": "vault_answerable"}))
        monkeypatch.setattr(intent_classifier, "ollama", fake)
        _stage3_llm_call("anything")
        call = fake.chat_calls[0]
        assert call.get("options", {}).get("think") is False
        assert call.get("format") == "json"

    def test_query_is_truncated_at_500_chars(self, monkeypatch):
        fake = FakeOllama(content=json.dumps({"label": "vault_answerable"}))
        monkeypatch.setattr(intent_classifier, "ollama", fake)
        _stage3_llm_call("x" * 10_000)
        user_content = fake.chat_calls[0]["messages"][1]["content"]
        assert len(user_content) == 500

    def test_invalid_json_returns_safe_default(self, monkeypatch):
        fake = FakeOllama(content="not JSON at all")
        monkeypatch.setattr(intent_classifier, "ollama", fake)
        assert _stage3_llm_call("any query") == "vault_answerable"

    def test_unknown_label_returns_safe_default(self, monkeypatch):
        fake = FakeOllama(content=json.dumps({"label": "maybe_internet"}))
        monkeypatch.setattr(intent_classifier, "ollama", fake)
        assert _stage3_llm_call("any query") == "vault_answerable"

    def test_ollama_exception_returns_safe_default(self, monkeypatch):
        fake = FakeOllama(exc=ConnectionError("Ollama unreachable"))
        monkeypatch.setattr(intent_classifier, "ollama", fake)
        assert _stage3_llm_call("any query") == "vault_answerable"


class TestStage3Timeout:
    """Stage 3 must honor the configured hard timeout."""

    def test_fast_llm_returns_label_with_timed_out_false(self, monkeypatch):
        monkeypatch.setattr(
            intent_classifier,
            "_stage3_llm_call",
            lambda q: "needs_internet",
        )
        label, timed_out = _stage3_classify_with_timeout("any query")
        assert label == "needs_internet"
        assert timed_out is False

    def test_slow_llm_times_out_with_safe_default(self, monkeypatch):
        import time

        def slow_call(query):
            time.sleep(2.0)
            return "needs_internet"

        monkeypatch.setattr(intent_classifier, "_stage3_llm_call", slow_call)
        # Override timeout to 100ms so the test doesn't wait forever.
        monkeypatch.setattr(
            intent_classifier,
            "get_intent_classifier_timeout_ms",
            lambda: 100,
        )
        label, timed_out = _stage3_classify_with_timeout("any query")
        assert label == "vault_answerable"
        assert timed_out is True


class TestClassifyIntentStage3Flow:
    """Stage 3 result flows through classify_intent's top-level log line."""

    def test_timeout_path_logs_stage_timeout(self, caplog, monkeypatch):
        # Force both earlier stages to escalate.
        _inject_stage2_cache(
            [
                ("needs_internet", [1.0, 0.0, 0.0]),
                ("vault_answerable", [0.0, 1.0, 0.0]),
            ]
        )
        monkeypatch.setattr(intent_classifier, "embed_text", lambda q: [0.3, 0.3, 0.9])
        # Simulate a Stage 3 timeout.
        monkeypatch.setattr(
            intent_classifier,
            "_stage3_classify_with_timeout",
            lambda q: ("vault_answerable", True),
        )
        query = "intentionally hard to classify without signal"
        with caplog.at_level("INFO", logger="ember.intent_classifier"):
            label = classify_intent(query)
        assert label == "vault_answerable"
        matches = [r for r in caplog.records if "[INTENT_CLASSIFY]" in r.message]
        assert len(matches) == 1
        assert "stage=timeout" in matches[0].message
        assert "label=vault_answerable" in matches[0].message

    def test_stage3_resolution_logs_stage_stage3(self, caplog, monkeypatch):
        _inject_stage2_cache(
            [
                ("needs_internet", [1.0, 0.0, 0.0]),
                ("vault_answerable", [0.0, 1.0, 0.0]),
            ]
        )
        monkeypatch.setattr(intent_classifier, "embed_text", lambda q: [0.3, 0.3, 0.9])
        monkeypatch.setattr(
            intent_classifier,
            "_stage3_classify_with_timeout",
            lambda q: ("needs_internet", False),
        )
        query = "something that only the LLM can resolve cleanly"
        with caplog.at_level("INFO", logger="ember.intent_classifier"):
            label = classify_intent(query)
        assert label == "needs_internet"
        matches = [r for r in caplog.records if "[INTENT_CLASSIFY]" in r.message]
        assert len(matches) == 1
        assert "stage=stage3" in matches[0].message


# ---------------------------------------------------------------------------
# B-WS-001: introspective uncertainty anchoring (Stage 2)
# ---------------------------------------------------------------------------

_INTROSPECTIVE_UNCERTAINTY_PHRASES = [
    "that's what I'm trying to figure out",
    "I'm still trying to figure that out",
    "I haven't figured that out yet",
    "I'm still wondering about that",
    "I'm trying to make sense of it",
    "I'm trying to wrap my head around it",
    "I'm not sure what to make of it",
    "I keep going back and forth on it",
    "I haven't been able to work that out",
    "that's what I've been trying to understand",
]


class TestIntrospectiveUncertaintyPoolInclusion:
    """The introspective-uncertainty anchor phrases must remain in the
    example pool. Catches accidental removal in a future refactor without
    needing Ollama. Non-Ollama-gated."""

    @pytest.mark.parametrize("phrase", _INTROSPECTIVE_UNCERTAINTY_PHRASES)
    def test_phrase_present_and_labeled_vault(self, phrase):
        match = next(
            (e for e in EXAMPLES if e["query"] == phrase),
            None,
        )
        assert match is not None, (
            f"introspective-uncertainty anchor missing from EXAMPLES: {phrase!r}"
        )
        assert match["label"] == "vault_answerable"


@_NEEDS_OLLAMA
class TestStage2IntrospectiveUncertainty:
    """End-to-end: the live classifier path must route introspective-
    uncertainty phrases to vault_answerable. Exercises Stage 2 via the
    real nomic-embed-text embedding similarity against the production
    example pool. If Stage 2 misses the 0.65 threshold, Stage 3 (qwen3:8b)
    runs and the legacy bug pattern returns."""

    @pytest.mark.parametrize("phrase", _INTROSPECTIVE_UNCERTAINTY_PHRASES)
    def test_phrase_routes_to_vault_answerable(self, phrase):
        assert classify_intent(phrase) == VAULT_ANSWERABLE


@_NEEDS_OLLAMA
class TestBWS001Regression:
    """Direct regression coverage for the B-WS-001 observed cases.

    The literal observed phrase must classify as vault_answerable. The
    contrastive imperative search ('Help me figure out X') must NOT be
    pulled toward vault_answerable by the introspective anchor; its
    verb structure and factual anchor should keep it on the
    needs_internet side."""

    def test_observed_phrase_routes_to_vault(self):
        assert classify_intent("That's what I'm trying to figure out.") == VAULT_ANSWERABLE

    def test_imperative_search_with_factual_anchor_still_routes_to_internet(self):
        # Contrastive control: same verb root ("figure out") but imperative
        # subject and concrete external anchor ("Python web framework in
        # 2026"). Must not collapse to vault_answerable.
        result = classify_intent(
            "Help me figure out the best Python web framework in 2026"
        )
        assert result != VAULT_ANSWERABLE


# ---------------------------------------------------------------------------
# B-CTX-001: first-person recall queries (Stage 2 misroute family)
# ---------------------------------------------------------------------------


@_NEEDS_OLLAMA
class TestBCTX001FirstPersonRecall:
    """B-CTX-001 surfaced three personal/conversational queries that Stage 2
    confidently misrouted to needs_internet on the Alex persona transcript:
    turns 7, 9, 10. After the v0.18.0 fix (removing the 'what do you know
    about X' counter-anchor), turn 7 routes correctly. Turns 9 and 10
    still misroute at Stage 2 against other needs_internet exemplars
    ('forecast for the east weekend', 'what is currently happening') —
    they are protected by the first-person guard in
    src/llm/ask_first_validator.py, not by the classifier.

    These tests document the current classifier behavior so a future
    Stage 2 rebalance or threshold change can target the residual
    misroute without regressing turn 7. Tests assert what the classifier
    SHOULD return on each query."""

    def test_turn7_what_you_know_about_me_routes_vault(self):
        # Removing 'what do you know about Rust' from the needs_internet
        # exemplars lets this query land on the vault-side anchor
        # 'what do you know about who I am'.
        result = classify_intent(
            "Connecting those two things, what do you actually know "
            "about me from this conversation?"
        )
        assert result == VAULT_ANSWERABLE, (
            f"turn 7 must route vault after Rust-anchor removal; got {result}"
        )

    @pytest.mark.xfail(
        reason=(
            "Turn 9 'what was I nervous about for this weekend' matches the "
            "needs_internet exemplar 'forecast for the east coast this "
            "weekend' on the 'this weekend' tail. Classifier-level fix is "
            "out of scope for v0.18.0; first-person guard in "
            "ask_first_validator protects the user-visible behavior. "
            "Document as residual classifier debt."
        ),
        strict=False,
    )
    def test_turn9_what_was_i_nervous_about_routes_vault(self):
        assert classify_intent("What was I nervous about for this weekend?") == VAULT_ANSWERABLE

    @pytest.mark.xfail(
        reason=(
            "Turn 10 'what are we discussing right now' matches the "
            "needs_internet exemplar cluster around 'currently' / "
            "'right now' temporal markers. Classifier-level fix is out "
            "of scope for v0.18.0; first-person guard protects the "
            "user-visible behavior. Document as residual classifier debt."
        ),
        strict=False,
    )
    def test_turn10_what_are_we_discussing_routes_vault(self):
        assert classify_intent("What are we discussing right now?") == VAULT_ANSWERABLE

    def test_turn8_what_profession_routes_vault(self):
        # Recall question that worked in the live B-CTX-001 run; lock it in.
        assert classify_intent("What profession did I tell you I have?") == VAULT_ANSWERABLE

    def test_general_knowledge_describe_still_routes_internet(self):
        # Counter-anchor preservation check: 'describe X' for an external
        # topic must still route to needs_internet despite the Rust
        # exemplar removal.
        result = classify_intent("describe how transformers work")
        assert result != VAULT_ANSWERABLE

    def test_general_knowledge_tell_me_about_still_routes_internet(self):
        result = classify_intent("tell me about quantum computing")
        assert result != VAULT_ANSWERABLE
